"""Controls for the firmware's fixed-bench motor_vibration test."""
import re

from PySide6.QtCore import Signal, QTimer, QSignalBlocker
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QDoubleSpinBox,
    QProxyStyle, QStyle, QVBoxLayout, QWidget)


DEFAULT_FILENAME = "imu_raw.bin"


class _PointHintStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
            return 150
        return super().styleHint(hint, option, widget, returnData)


class VibrationPanel(QWidget):
    command = Signal(str)
    parameter_request = Signal(str, object)

    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('仅用于固定台架。测试会驱动电机；断开连接不会代替中止。'))
        form = QFormLayout()
        layout.addLayout(form)
        self.mode = QComboBox()
        self.mode.addItems(['主电机', '尾电机', '主尾配对'])
        form.addRow('测试模式', self.mode)
        points = QHBoxLayout()
        self.first, self.last = QSpinBox(), QSpinBox()
        for spin in (self.first, self.last):
            spin.setRange(1, 20)
        points.addWidget(self.first)
        self.points_separator = QLabel('至')
        points.addWidget(self.points_separator)
        points.addWidget(self.last)
        self.first.valueChanged.connect(self.last.setMinimum)
        self.points_label = QLabel('连续测点（起止相同即单点）')
        form.addRow(self.points_label, points)
        self._point_style = _PointHintStyle()
        self._point_style.setParent(self)
        for widget in (self.points_label, self.points_separator, self.first, self.last,
                       self.first.lineEdit(), self.last.lineEdit()):
            widget.setStyle(self._point_style)
        self.first.valueChanged.connect(self.update_point_hints)
        self.last.valueChanged.connect(self.update_point_hints)
        self.mode.currentIndexChanged.connect(self.update_point_hints)
        self.update_point_hints()
        self.save = QCheckBox('保存波形到飞控 SD 卡')
        form.addRow(self.save)
        self.name = QLineEdit(DEFAULT_FILENAME)
        self.name.setPlaceholderText(f'留空使用 {DEFAULT_FILENAME}')
        self.name.setEnabled(False)
        self.save.toggled.connect(self.name.setEnabled)
        form.addRow('文件名', self.name)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        def add(text, callback):
            btn = QPushButton(text)
            actions.addWidget(btn)
            btn.clicked.connect(callback)
            return btn
        self.prepare = add('启动产测模块', lambda: self.command.emit('factory_test start'))
        self.begin = add('开始振动测试…', self.start_test)
        self.status = add('查询状态', lambda: self.command.emit('factory_test status'))
        self.abort = add('中止测试', lambda: self.command.emit('factory_test abort'))
        header = QHBoxLayout()
        self.trim_status = QLabel('舵机调整')
        header.addWidget(self.trim_status, 1)
        self.refresh_trim = QPushButton('读取舵机参数')
        header.addWidget(self.refresh_trim)
        layout.addLayout(header)
        self.trim_fields = QWidget()
        self.trim_form = QFormLayout(self.trim_fields)
        self.trim_form.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.trim_fields)
        self.trims = {}
        self.trim_available = False
        self.pending = None
        self.trim_timer = QTimer(self, singleShot=True, interval=3000)
        self.trim_timer.timeout.connect(lambda: self.reset_trims('未收到飞控确认，请重新读取'))
        self.refresh_trim.clicked.connect(self.read_trims)
        self.refresh_trim.setEnabled(False)
        layout.addWidget(QLabel('飞控 NSH 回显（是否已启动、完成或中止，以飞控状态为准）'))
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(2000)
        layout.addWidget(self.output)

        if settings is not None:
            for key, widget, default in [('mode', self.mode, 0), ('first', self.first, 1), ('last', self.last, 1)]:
                try:
                    value = int(settings.value('vibration/' + key, default))
                except (TypeError, ValueError):
                    value = default
                if key == 'mode':
                    widget.setCurrentIndex(value if 0 <= value < widget.count() else default)
                else:
                    widget.setValue(value)
            self.save.setChecked(settings.value('vibration/save', False, type=bool))
            self.name.setText(settings.value('vibration/name', DEFAULT_FILENAME, type=str) or DEFAULT_FILENAME)

    def update_point_hints(self):
        motor = '尾电机' if self.mode.currentIndex() == 1 else '主电机'
        rule = f'{motor}推力指令每点增加 5 个百分点，不对应固定电压。'
        if self.mode.currentIndex() == 2:
            rule += '\n尾电机指令由配对曲线决定。'
        descriptions = []
        for spin in (self.first, self.last):
            point = spin.value()
            description = f'第 {point} 点：{point * 5}%'
            spin.setToolTip(description)
            descriptions.append(description)
        selection = descriptions[0] if self.first.value() == self.last.value() else ' 至 '.join(descriptions)
        self.points_separator.setToolTip(selection)
        self.points_label.setToolTip(selection + '\n' + rule)

    def remember(self):
        if self.settings is None:
            return
        for key, value in [('mode', self.mode.currentIndex()), ('first', self.first.value()),
                           ('last', self.last.value()), ('save', self.save.isChecked()),
                           ('name', self.name.text() or DEFAULT_FILENAME)]:
            self.settings.setValue('vibration/' + key, value)
        self.settings.sync()

    def set_available(self, available):
        for btn in (self.prepare, self.begin, self.status, self.abort):
            btn.setEnabled(available)
        if available != self.trim_available:
            self.trim_available = available
            self.reset_trims('')
            if available and self.isVisible():
                self.read_trims()

    def showEvent(self, event):
        super().showEvent(event)
        if self.trim_available and self.pending is None:
            self.read_trims()

    def reset_trims(self, message):
        self.trim_timer.stop()
        self.pending = None
        self.trims.clear()
        while self.trim_form.rowCount():
            self.trim_form.removeRow(0)
        self.trim_status.setText('舵机调整' + (' · ' + message if message else ''))
        self.refresh_trim.setEnabled(self.trim_available)

    def request_param(self, name, value=None):
        self.pending = (name, value)
        self.refresh_trim.setEnabled(False)
        self.trim_fields.setEnabled(False)
        self.trim_status.setText(f'舵机调整 · {"读取" if value is None else "等待写入确认"} {name}')
        self.trim_timer.start()
        self.parameter_request.emit(name, value)

    def read_trims(self):
        if self.trim_available and self.pending is None:
            self.reset_trims('')
            self.request_param('CA_SP0_COUNT')

    def parameter_received(self, name, value):
        if self.pending is None or self.pending[0] != name:
            return
        _, requested = self.pending
        self.pending = None
        self.trim_timer.stop()
        if name == 'CA_SP0_COUNT':
            if value not in (2, 3, 4):
                self.reset_trims('舵机数量无效，请检查 CA_SP0_COUNT')
                return
            self.trim_count = int(value)
            self.request_param('CA_SV_CS0_TRIM')
            return
        if not -1 <= value <= 1:
            self.reset_trims(f'{name} 超出范围，请检查参数')
            return
        if name not in self.trims:
            spin = QDoubleSpinBox()
            spin.setRange(-1, 1)
            spin.setSingleStep(0.01)
            spin.setKeyboardTracking(False)
            spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
            spin.setToolTip('输入 −1.00 至 1.00；回车或离开输入框时提交')
            row = QHBoxLayout()
            row.addWidget(spin)
            for symbol, hint, step in [('▲', '增加 0.01', spin.stepUp), ('▼', '减少 0.01', spin.stepDown)]:
                button = QPushButton(symbol)
                button.setFixedSize(36, 26)
                font = button.font()
                font.setPixelSize(14)
                button.setFont(font)
                button.setToolTip(hint)
                button.setAccessibleName(f'{name} {hint}')
                button.clicked.connect(step)
                row.addWidget(button)
            row.addStretch()
            self.trim_form.addRow(name, row)
            self.trims[name] = spin
            spin.valueChanged.connect(lambda v, n=name: self.write_trim(n, v))
        spin = self.trims[name]
        with QSignalBlocker(spin):
            spin.setValue(value)
        if len(self.trims) < self.trim_count:
            self.request_param(f'CA_SV_CS{len(self.trims)}_TRIM')
            return
        self.trim_fields.setEnabled(True)
        self.refresh_trim.setEnabled(True)
        status = f'{self.trim_count} 个舵机' if requested is None else (
            f'{name} 已确认' if abs(value - requested) < 0.00001 else f'{name} 写入不符，已显示飞控值')
        self.trim_status.setText('舵机调整 · ' + status)

    def write_trim(self, name, value):
        if self.trim_available and self.pending is None and name in self.trims:
            self.request_param(name, value)

    def start_test(self):
        first, last = self.first.value(), self.last.value()
        cmd = f'factory_test begin motor_vibration {first} {last} {self.mode.currentIndex()}'
        saving = '不保存波形'
        if self.save.isChecked():
            name = self.name.text() or DEFAULT_FILENAME
            if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,40}', name) or name.endswith('.'):
                QMessageBox.warning(self, '文件名无效', '限 1～40 个英文字母、数字、点、下划线或短横线，不能以点结尾。')
                return
            cmd += ' ' + name
            saving = f'保存为 {name}（飞控上同名文件会被替换）'
        choice = QMessageBox.question(self, '确认台架测试',
            f'{self.mode.currentText()}，测点 {first} 至 {last}，{saving}。\n'
            '测试会驱动电机。确认机体已固定、人员已远离并可现场停机？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if choice == QMessageBox.StandardButton.Yes and self.begin.isEnabled():
            self.remember()
            self.command.emit(cmd)
