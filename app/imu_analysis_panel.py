"""Analysis page: background reports, preview lifetime and export."""
import csv
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from imu_compare import append_reports
from imu_fft import fft_curves, render_fft
from imu_plot_view import CurvePreview
from imu_point_stats import AXIS_PLOTS
from imu_ui import button, last_dir, restore_tab
from imu_workbench import copy_report
from imu_workers import Analysis
from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class FFTView(QWidget):
    def __init__(self, waveforms, calculate):
        super().__init__()
        self.curves = fft_curves(waveforms)
        self.preview = None
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.curve, self.point, self.sensor = QComboBox(), QComboBox(), QComboBox()
        for label, box in [('曲线', self.curve), ('转速点', self.point), ('传感器', self.sensor)]:
            row.addWidget(QLabel(label))
            row.addWidget(box, 1)
        self.point.setToolTip('实测平均转速及其对应的转频：Hz = RPM ÷ 60。')
        self.calculate = button(row, '计算 FFT 和 Welch', calculate)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.plot_area = QVBoxLayout()
        layout.addLayout(self.plot_area, 1)
        self.curve.addItems([curve.label for curve in self.curves])
        self.curve.currentIndexChanged.connect(self.update_points)
        self.point.currentIndexChanged.connect(self.update_sensors)
        self.sensor.currentIndexChanged.connect(self.reset_preview)
        self.update_points()

    def update_points(self):
        self.point.clear()
        if self.curve.currentIndex() >= 0:
            for point, rpm, _ in self.curves[self.curve.currentIndex()].points:
                speed = '转速缺测' if rpm is None else f'{rpm:.1f} RPM（{rpm / 60:.2f} Hz）'
                self.point.addItem(f'测点 {point} · {speed}', point)
        self.update_sensors()

    def update_sensors(self):
        self.sensor.clear()
        if self.point.currentIndex() >= 0:
            _, _, sensors = self.curves[self.curve.currentIndex()].points[self.point.currentIndex()]
            for key, name in [('accel', '加速度计'), ('gyro', '陀螺仪')]:
                if key in sensors:
                    self.sensor.addItem(name, key)
        self.reset_preview()

    def reset_preview(self):
        if self.preview is not None:
            self.plot_area.removeWidget(self.preview)
            self.preview.deleteLater()
            self.preview = None
        ready = self.sensor.currentIndex() >= 0
        self.calculate.setEnabled(ready)
        self.status.setText('选好曲线和测点后，点击“计算 FFT 和 Welch”。' if ready else '没有可分析的采样。')

    def show_result(self, _, message, figures):
        self.preview = CurvePreview(figures['fft'], min_plot_height=1050)
        self.plot_area.addWidget(self.preview)
        self.status.setText(message)


class AnalysisPanel(QWidget):
    activated = Signal()
    notice = Signal(str, int)
    warning = Signal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.task = self.report = self.saved_report = self.preview = None
        self.fft = None
        self.waveforms = {}
        local = QVBoxLayout(self)
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
        self.controls()

    def controls(self):
        self.clear_btn.setEnabled(self.task is None and self.report is not None)
        self.choose.setEnabled(self.task is None)
        if self.fft is not None:
            self.fft.setEnabled(self.task is None)
        self.save_report_btn.setEnabled(self.report is not None and self.task is None)
        self.open_report.setEnabled(self.saved_report is not None)

    def open_local(self):
        names, _ = QFileDialog.getOpenFileNames(self, '选择要追加的波形或采样 CSV',
            str(last_dir(self.settings, 'analysis/openDir')), '日志 (*.bin *.ulg *.csv);;所有文件 (*)')
        if not names:
            return
        paths = [p for p in dict.fromkeys(Path(n).resolve() for n in names) if p not in self.waveforms]
        if not paths:
            self.notice.emit('所选波形已载入，无需重复追加。', 4000)
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
        self.fft = None
        while self.views.count():
            widget = self.views.widget(0)
            self.views.removeTab(0)
            widget.deleteLater()

    def clear_waveforms(self):
        self.remember_view()
        self.clear_views()
        self.waveforms.clear()
        self.report = self.saved_report = None
        self.cleanup_preview()
        self.info.setText('已清空。可重新选择波形或采样 CSV。')
        self.controls()

    def save_report(self):
        folder = QFileDialog.getExistingDirectory(self, '选择报告保存位置',
            str(last_dir(self.settings, 'analysis/saveDir', self.source.parent)))
        if not folder:
            return
        try:
            target = copy_report(self.report, Path(folder), self.source)
        except ValueError as exc:
            self.warning.emit(str(exc))
            return
        except OSError as exc:
            self.warning.emit(f'保存失败：{exc}')
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

    def start_fft(self):
        view = self.fft
        if self.task is not None or view is None or view.sensor.currentIndex() < 0:
            return
        view.reset_preview()
        path = view.curves[view.curve.currentIndex()].samples
        task = Analysis(path, view.point.currentData(), view.sensor.currentData(), operation=render_fft)
        self.run_task(task, view.show_result, '正在计算所选测点的 FFT 和 Welch…', status=view.status)

    def run_task(self, task, on_result, message, *, status=None):
        self.remember_view()
        self.activated.emit()
        if status is None:
            status = self.info
        self.task_status = status
        status.setText(message)
        self.task = task
        task.result.connect(on_result)
        task.error.connect(self.analysis_error)
        task.finished.connect(self.analysis_done)
        self.controls()
        task.start()

    def analysis_error(self, text):
        self.task_status.setText("分析失败：" + text)

    def analysis_done(self):
        self.task_status = None
        self.task.deleteLater()
        self.task = None
        self.controls()

    def show_report(self, folder, message, figures):
        self.report = folder
        self.report_message = message
        self.clear_views()
        self.info.setText(f"{message}\n" + (str(folder) if self.saved_report else '当前为预览，点击“保存分析结果”可保留报告。'))
        for filename, title in [
            ("main_rpm.png", "主电机曲线"),
            ("tail_rpm.png", "尾电机曲线"),
            *((name, f"主电机 {axis.upper()} 轴统计") for name, (axis,) in AXIS_PLOTS["main_rpm"].items()),
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
        self.fft = FFTView(self.waveforms, self.start_fft)
        self.views.addTab(self.fft, 'FFT 分析')
        preferred = self.settings.value('analysis/view', '', type=str)
        restore_tab(self.views, preferred)
        self.controls()

    def cleanup_preview(self):
        if self.preview is not None:
            self.preview.cleanup()
            self.preview = None
