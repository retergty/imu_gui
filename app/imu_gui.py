"""Cross-platform factory IMU workbench."""

import signal
import sys
from pathlib import Path

from imu_analysis_panel import AnalysisPanel
from imu_test_panel import VibrationPanel
from imu_theme import apply_theme
from imu_ui import button, last_dir, restore_tab
from imu_vibration_download import REMOTE_DIR
from imu_workers import Connection
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from serial.tools.list_ports import comports


def valid_baud(text):
    return text.isascii() and text.isdecimal() and int(text) > 0


class Window(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings("IMUWorkbench", "Desktop")
        self.setWindowTitle("飞控 IMU 工作台")
        self.resize(1000, 700)
        self.link = None
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
        remote = self.page("飞控日志")
        remote.addWidget(QLabel(f"飞控目录：{REMOTE_DIR}"))
        self.entries = QListWidget()
        remote.addWidget(self.entries)
        row = QHBoxLayout()
        remote.addLayout(row)
        self.refresh = button(row, "读取目录", self.list_files)
        self.get = button(row, "下载选中文件…", self.download)
        self.auto = QCheckBox("下载后分析")
        row.addWidget(self.auto)
        self.progress = QProgressBar()
        remote.addWidget(self.progress)
        self.analysis = AnalysisPanel(self.settings)
        self.tabs.addTab(self.analysis, "数据分析")
        self.analysis.activated.connect(lambda: self.tabs.setCurrentWidget(self.analysis))
        self.analysis.warning.connect(self.warn)
        self.analysis.notice.connect(self.statusBar().showMessage)
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
        if valid_baud(baud):
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
        restore_tab(self.tabs, previous)
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

    def warn(self, text):
        QMessageBox.warning(self, "工作台提示", text)

    def remember_connection(self):
        endpoint = self.device.currentText().strip()
        baud = self.baud.currentText().strip()
        if endpoint and valid_baud(baud):
            self.settings.setValue("connection/endpoint", endpoint)
            self.settings.setValue("connection/baud", baud)
            self.settings.sync()

    def connect_device(self):
        if self.link:
            self.link.requestInterruption()
            self.connect_btn.setEnabled(False)
            return
        endpoint = self.device.currentText().strip()
        baud = self.baud.currentText().strip()
        if not endpoint or not valid_baud(baud):
            self.warn("请填写端口和有效的波特率。")
            return
        self.remember_connection()
        worker = self.link = Connection(endpoint, int(baud))
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
            self, "保存日志", str(last_dir(self.settings, 'download/dir') / entry.name)
        )
        if name:
            self.settings.setValue('download/dir', str(Path(name).parent))
            self.progress.setValue(0)
            self.enqueue("get", (entry, Path(name)))

    def saved(self, path):
        self.statusBar().showMessage(f"下载完成：{path}")
        if self.auto.isChecked():
            if self.analysis.task:
                self.warn("日志已保存。数据分析正忙，请稍后手动打开。")
            else:
                self.analysis.start_analysis(path, path.parent)

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

    def closeEvent(self, event):
        if self.busy or self.analysis.task:
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
        self.analysis.remember_view()
        self.analysis.cleanup_preview()
        self.settings.setValue('ui/tabName', self.tabs.tabText(self.tabs.currentIndex()))
        self.settings.setValue('download/analyze', self.auto.isChecked())
        self.settings.setValue('shell/history', [self.command.itemText(i) for i in range(min(50, self.command.count()))])
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
