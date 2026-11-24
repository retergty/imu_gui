"""Cross-platform factory IMU workbench."""

import csv
import shutil
import signal
import sys
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)
from serial.tools.list_ports import comports
from imu_compare import append_reports
from imu_test_panel import VibrationPanel
from imu_theme import apply_theme
from imu_workers import Analysis, Connection


def button(layout, title, callback):
    btn = QPushButton(title)
    btn.clicked.connect(callback)
    layout.addWidget(btn)
    return btn


class CurvePreview(QLabel):
    def __init__(self, figure):
        super().__init__()
        self.figure = figure
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setToolTip('点击图例可隐藏或恢复对应曲线，仅影响当前预览。')
        self.refresh_image()

    def refresh_image(self):
        canvas = self.figure.canvas
        rgba = canvas.buffer_rgba()  # Already drawn when the report PNG was saved.
        h, w, _ = rgba.shape
        self.original = QPixmap.fromImage(QImage(rgba, w, h, 4 * w, QImage.Format.Format_RGBA8888))
        self.fit_image()

    def fit_image(self):
        size = self.contentsRect().size()
        if not size.isEmpty():
            self.setPixmap(self.original.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_image()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.pixmap().isNull():
            return super().mousePressEvent(event)
        # The centered preview may be letterboxed. Convert Qt coordinates (top left)
        # to the Agg renderer's coordinates (bottom left) before testing the legend.
        size = self.pixmap().size()
        rect = self.contentsRect()
        left = rect.x() + (rect.width() - size.width()) / 2
        top = rect.y() + (rect.height() - size.height()) / 2
        x = (event.position().x() - left) * self.original.width() / size.width()
        y = (1 - (event.position().y() - top) / size.height()) * self.original.height()
        renderer = self.figure.canvas.get_renderer()
        for ax in self.figure.axes:
            legend = ax.get_legend()
            if legend is None:
                continue
            for text, handle in zip(legend.get_texts(), legend.legend_handles):
                box = text.get_window_extent(renderer)
                # Include the swatch and the gap before its label (also for scatter handles).
                swatch = renderer.points_to_pixels(text.get_fontsize()) * (legend.handlelength + legend.handletextpad)
                if box.x0 - swatch <= x <= box.x1 and box.y0 <= y <= box.y1:
                    label = text.get_text()
                    artists = [a for a in (*ax.lines, *ax.collections)
                               if a.get_label() == label or a.get_gid() == label]
                    if artists:
                        visible = not artists[0].get_visible()
                        for artist in artists:
                            artist.set_visible(visible)
                        for item in (handle, text):
                            item.set_alpha(1 if visible else .35)
                        self.figure.canvas.draw()
                        self.refresh_image()
                    return
        super().mousePressEvent(event)


class Window(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings("IMUWorkbench", "Desktop")
        self.setWindowTitle("飞控 IMU 工作台")
        self.resize(1000, 700)
        self.link = self.analysis = self.report = None
        self.waveforms = {}
        self.preview = self.saved_report = None
        self.online = self.busy = False
        root = QWidget(objectName="workbench")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(9)
        heading = QHBoxLayout()
        title = QLabel(self.windowTitle())
        title_font = title.font()
        title_font.setPointSizeF(title_font.pointSizeF() + 6)
        title_font.setBold(True)
        title.setFont(title_font)
        heading.addWidget(title, 1)
        self.state = QLabel(objectName="connectionState")
        heading.addWidget(self.state)
        layout.addLayout(heading)
        bar = QHBoxLayout()
        layout.addLayout(bar)
        bar.addWidget(QLabel("端口 / MAVLink 地址"))
        self.device = QComboBox()
        self.device.setEditable(True)
        bar.addWidget(self.device, 1)
        self.scan = button(bar, "刷新串口", self.ports)
        self.baud = QComboBox()
        self.baud.addItems(["115200", "57600", "230400", "460800", "921600"])
        self.baud.setEditable(True)
        bar.addWidget(QLabel("波特率"))
        bar.addWidget(self.baud)
        self.connect_btn = button(bar, "连接", self.connect_device)
        self.connect_btn.setObjectName("connectButton")
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.vibration = VibrationPanel(self.settings)
        self.tabs.addTab(self.vibration, "IMU 振动测试")
        self.vibration.command.connect(lambda cmd: self.enqueue("shell", cmd + "\n"))
        self.vibration.parameter_request.connect(lambda name, value: self.enqueue('parameter', (name, value)))
        self.remote = self.page("飞控日志")
        self.remote.addWidget(QLabel("飞控目录：/fs/microsd/log/imu_vibration"))
        self.entries = QListWidget()
        self.remote.addWidget(self.entries)
        row = QHBoxLayout()
        self.remote.addLayout(row)
        self.refresh = button(row, "读取目录", self.list_files)
        self.get = button(row, "下载选中文件…", self.download)
        self.auto = QCheckBox("下载后分析")
        row.addWidget(self.auto)
        self.progress = QProgressBar()
        self.remote.addWidget(self.progress)
        local = self.page("数据分析")
        row = QHBoxLayout()
        local.addLayout(row)
        self.choose = button(row, "打开波形 / CSV 并分析…", self.open_local)
        self.clear_btn = button(row, "清空波形", self.clear_waveforms)
        self.save_report_btn = button(row, "保存分析结果…", self.save_report)
        self.open_report = button(
            row,
            "打开结果目录",
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.saved_report))),
        )
        self.info = QLabel('可重复打开波形或采样 CSV，按实测 RPM 叠加对比。')
        self.info.setWordWrap(True)
        local.addWidget(self.info)
        self.views = QTabWidget()
        local.addWidget(self.views)
        shell = self.page("NSH 控制台")
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)
        shell.addWidget(self.console)
        row = QHBoxLayout()
        shell.addLayout(row)
        self.command = QComboBox()
        self.command.setEditable(True)
        self.command.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        row.addWidget(self.command, 1)
        self.command.lineEdit().returnPressed.connect(self.send)
        self.send_btn = button(row, "发送", self.send)
        self.ctrl = button(row, "Ctrl+C", lambda: self.enqueue("shell", "\x03"))
        self.entries.itemSelectionChanged.connect(self.controls)
        self.ports()
        endpoint = self.settings.value("connection/endpoint", "", type=str)
        if endpoint:
            self.device.setCurrentText(endpoint)
        baud = self.settings.value("connection/baud", "115200", type=str)
        if baud.isascii() and baud.isdecimal() and int(baud) > 0:
            self.baud.setCurrentText(baud)
        self.device.lineEdit().setPlaceholderText("选择串口，或输入 MAVLink 地址")
        previous = self.settings.value('ui/tabName', '', type=str)
        if not previous and self.settings.contains('ui/tab'):
            old_order = ('飞控日志', 'NSH 控制台', '数据分析', 'IMU 振动测试')
            index = self.settings.value('ui/tab', -1, type=int)
            if 0 <= index < len(old_order):
                previous = old_order[index]
        if previous == "本地分析":
            previous = "数据分析"
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == previous:
                self.tabs.setCurrentIndex(i)
                break
        self.auto.setChecked(self.settings.value('download/analyze', False, type=bool))
        self.command.addItems(self.settings.value('shell/history', [], type=list))
        self.command.setCurrentIndex(-1)
        self.controls()

    def page(self, name):
        widget = QWidget()
        self.tabs.addTab(widget, name)
        return QVBoxLayout(widget)

    def ports(self):
        previous = self.device.currentText()
        self.device.clear()
        self.device.addItems([p.device for p in comports()])
        if previous:
            self.device.setCurrentText(previous)

    def controls(self):
        free = self.online and not self.busy
        self.vibration.set_available(free)
        for w in (self.refresh, self.command, self.send_btn, self.ctrl):
            w.setEnabled(free)
        self.get.setEnabled(free and self.entries.currentItem() is not None)
        for w in (self.device, self.scan, self.baud):
            w.setEnabled(self.link is None)
        self.connect_btn.setText("断开" if self.link else "连接")
        self.connect_btn.setEnabled(self.link is None or free)
        self.state.setText(
            "传输中 · NSH 暂停"
            if self.busy
            else "已连接"
            if self.online
            else "连接中…"
            if self.link
            else "未连接"
        )
        self.clear_btn.setEnabled(self.analysis is None and self.report is not None)
        self.choose.setEnabled(self.analysis is None)
        self.save_report_btn.setEnabled(self.report is not None and self.analysis is None)
        self.open_report.setEnabled(self.saved_report is not None)

    def warn(self, text):
        QMessageBox.warning(self, "工作台提示", text)

    def remember_connection(self):
        endpoint = self.device.currentText().strip()
        baud = self.baud.currentText().strip()
        if endpoint and baud.isascii() and baud.isdecimal() and int(baud) > 0:
            self.settings.setValue("connection/endpoint", endpoint)
            self.settings.setValue("connection/baud", baud)
            self.settings.sync()

    def connect_device(self):
        if self.link:
            self.link.requestInterruption()
            self.connect_btn.setEnabled(False)
            return
        endpoint = self.device.currentText().strip()
        if (
            not endpoint
            or not self.baud.currentText().isdigit()
            or int(self.baud.currentText()) <= 0
        ):
            self.warn("请填写端口和有效的波特率。")
            return
        self.remember_connection()
        worker = self.link = Connection(endpoint, int(self.baud.currentText()))
        worker.ready.connect(self.connected)
        worker.finished.connect(self.disconnected)
        worker.output.connect(self.append_output)
        worker.parameter.connect(self.vibration.parameter_received)
        worker.progress.connect(self.progress.setValue)
        worker.files.connect(self.show_files)
        worker.saved.connect(self.saved)
        worker.error.connect(self.warn)
        worker.idle.connect(self.release)
        self.console.clear()
        self.vibration.output.clear()
        self.controls()
        worker.start()

    def connected(self):
        self.online = True
        self.controls()

    def disconnected(self):
        self.link.deleteLater()
        self.link = None
        self.online = self.busy = False
        self.vibration.reset_status_connection()
        self.entries.clear()
        self.controls()

    def release(self):
        self.busy = False
        self.controls()

    def enqueue(self, action, args=None):
        if not self.online or self.busy:
            return
        if action not in ("shell", "parameter"):
            self.busy = True
        self.controls()
        self.link.jobs.put((action, args))

    def list_files(self):
        self.entries.clear()
        self.enqueue("list")

    def show_files(self, files):
        for entry in files:
            if (
                not entry.name
                or "/" in entry.name
                or "\\" in entry.name
                or ":" in entry.name
                or entry.name in (".", "..")
            ):
                continue
            item = QListWidgetItem(f"{entry.name}    {entry.size_b:,} B")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.entries.addItem(item)
        self.statusBar().showMessage(f"远端文件：{self.entries.count()} 个")

    def download(self):
        item = self.entries.currentItem()
        if not item:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        name, _ = QFileDialog.getSaveFileName(
            self, "保存日志", str(self.last_dir('download/dir') / entry.name)
        )
        if name:
            self.settings.setValue('download/dir', str(Path(name).parent))
            self.progress.setValue(0)
            self.enqueue("get", (entry, Path(name)))

    def saved(self, path):
        self.statusBar().showMessage(f"下载完成：{path}")
        if self.auto.isChecked():
            if self.analysis:
                self.warn("日志已保存。数据分析正忙，请稍后手动打开。")
            else:
                self.start_analysis(path, path.parent)

    def send(self):
        text = self.command.currentText()
        self.enqueue("shell", text + "\n")
        if text and self.command.findText(text) < 0:
            self.command.insertItem(0, text)
        self.command.setCurrentIndex(-1)

    def append_output(self, text):
        self.vibration.receive_shell(text)
        self.console.moveCursor(QTextCursor.MoveOperation.End)
        self.console.insertPlainText(text.replace("\r", ""))
        self.console.ensureCursorVisible()

    def last_dir(self, key, fallback=None):
        path = Path(self.settings.value(key, str(fallback or Path.home()), type=str))
        return path if path.is_dir() else Path.home()

    def open_local(self):
        names, _ = QFileDialog.getOpenFileNames(self, '选择要追加的波形或采样 CSV',
            str(self.last_dir('analysis/openDir')), '日志 (*.bin *.ulg *.csv);;所有文件 (*)')
        if not names:
            return
        paths = [p for p in dict.fromkeys(Path(n).resolve() for n in names) if p not in self.waveforms]
        if not paths:
            self.statusBar().showMessage('所选波形已载入，无需重复追加。', 4000)
            return
        self.settings.setValue('analysis/openDir', str(paths[0].parent))
        if self.preview is None:
            self.preview = TemporaryDirectory(prefix='imu_preview_')
        self.run_task(Analysis(self.waveforms, paths, Path(self.preview.name), operation=append_reports),
                      self.append_done, f'正在追加 {len(paths)} 份波形…')

    def append_done(self, result, message, figures):
        folder, reports = result
        if self.report and self.report not in self.waveforms.values() and self.preview and self.report.parent == Path(self.preview.name):
            shutil.rmtree(self.report, ignore_errors=True)
        self.waveforms = reports
        self.source = next(iter(reports))
        self.saved_report = None
        self.show_report(folder, message, figures)

    def clear_views(self):
        while self.views.count():
            widget = self.views.widget(0)
            self.views.removeTab(0)
            widget.deleteLater()

    def clear_waveforms(self):
        self.remember_view()
        self.clear_views()
        self.waveforms.clear()
        self.report = self.saved_report = None
        if self.preview is not None:
            self.preview.cleanup()
            self.preview = None
        self.info.setText('已清空。可重新选择波形或采样 CSV。')
        self.controls()

    def save_report(self):
        folder = QFileDialog.getExistingDirectory(self, '选择报告保存位置',
            str(self.last_dir('analysis/saveDir', self.source.parent)))
        if not folder:
            return
        if Path(folder).resolve().is_relative_to(self.report.resolve()):
            self.warn('请选择当前报告目录以外的位置。')
            return
        target = None
        try:
            target = Path(mkdtemp(prefix=f'{self.source.stem}_stats_', dir=folder))
            shutil.copytree(self.report, target, dirs_exist_ok=True)
        except OSError as exc:
            if target is not None:
                shutil.rmtree(target, ignore_errors=True)
            self.warn(f'保存失败：{exc}')
            return
        self.settings.setValue('analysis/saveDir', folder)
        self.saved_report = target
        self.info.setText(f'{self.report_message}\n已保存：{target}')
        self.controls()

    def remember_view(self):
        i = self.views.currentIndex()
        if i >= 0:
            self.settings.setValue('analysis/view', self.views.tabText(i))

    def start_analysis(self, path, folder):
        # Download-and-analyze keeps its persistent report beside the download.
        self.clear_waveforms()
        self.source = path
        self.run_task(Analysis(path, folder), self.download_report, f'正在处理：{path}')

    def download_report(self, folder, message, figures):
        self.waveforms = {self.source.resolve(): folder}
        self.saved_report = folder
        self.show_report(folder, message, figures)

    def run_task(self, task, on_result, message):
        self.remember_view()
        self.tabs.setCurrentIndex(2)
        self.info.setText(message)
        self.analysis = task
        task.result.connect(on_result)
        task.error.connect(self.analysis_error)
        task.finished.connect(self.analysis_done)
        self.controls()
        task.start()

    def analysis_error(self, text):
        self.info.setText("分析失败：" + text)

    def analysis_done(self):
        self.analysis.deleteLater()
        self.analysis = None
        self.controls()

    def show_report(self, folder, message, figures):
        self.report = folder
        self.report_message = message
        self.clear_views()
        self.info.setText(f"{message}\n" + (str(folder) if self.saved_report else '当前为预览，点击“保存分析结果”可保留报告。'))
        for filename, title in [
            ("main_rpm.png", "主电机曲线"),
            ("tail_rpm.png", "尾电机曲线"),
            ("main_rpm_mean_rms.png", "主电机分轴统计"),
            ("tail_rpm_mean_rms.png", "尾电机分轴统计"),
        ]:
            if filename in figures:
                self.views.addTab(CurvePreview(figures[filename]), title)
        for filename, title in [("main_rpm.csv", "主电机表"), ("tail_rpm.csv", "尾电机表"), ("summary.csv", "分轴统计表")]:
            if not (folder / filename).exists():
                continue
            with (folder / filename).open(encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                headers = next(reader)
                table = QTableWidget(0, len(headers))
                table.setHorizontalHeaderLabels(headers)
                table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
                for i, values in enumerate(reader):
                    table.insertRow(i)
                    for j, value in enumerate(values):
                        table.setItem(i, j, QTableWidgetItem(value))
                table.resizeColumnsToContents()
                self.views.addTab(table, title)
        preferred = self.settings.value('analysis/view', '', type=str)
        for i in range(self.views.count()):
            if self.views.tabText(i) == preferred:
                self.views.setCurrentIndex(i)
                break
        self.controls()

    def closeEvent(self, event):
        if self.busy or self.analysis:
            self.statusBar().showMessage("任务执行中，请在任务结束后关闭。")
            event.ignore()
            return
        if self.link:
            self.link.requestInterruption()
            if not self.link.wait(500):
                self.statusBar().showMessage("正在断开，请稍后关闭。")
                event.ignore()
                return
        self.remember_connection()
        self.remember_view()
        self.settings.setValue('ui/tabName', self.tabs.tabText(self.tabs.currentIndex()))
        self.settings.setValue('download/analyze', self.auto.isChecked())
        self.settings.setValue('shell/history', [self.command.itemText(i) for i in range(min(50, self.command.count()))])
        if self.preview is not None:
            self.preview.cleanup()
        self.vibration.remember()
        event.accept()


def main():
    app = QApplication(sys.argv)
    apply_theme(app)
    window = Window()
    window.show()
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(0, app.quit)
        return app.exec()
    # Let the interpreter service SIGINT even while the native Qt loop is idle.
    interrupted = False

    def request_close(signum, frame):
        nonlocal interrupted
        interrupted = True

    def check_interrupt():
        nonlocal interrupted
        if interrupted:
            interrupted = False
            window.close()  # Follow the same busy/worker checks as the close button.

    timer = QTimer(window)
    timer.timeout.connect(check_interrupt)
    previous = signal.signal(signal.SIGINT, request_close)
    timer.start(150)
    try:
        return app.exec()
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    sys.exit(main())
