"""Analysis page: background reports, preview lifetime and export."""
import csv
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from imu_compare import append_reports
from imu_point_stats import AXIS_PLOTS
from imu_ui import button, last_dir, restore_tab
from imu_workbench import copy_report
from imu_workers import Analysis
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


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


class AnalysisPanel(QWidget):
    activated = Signal()
    notice = Signal(str, int)
    warning = Signal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.task = self.report = self.saved_report = self.preview = None
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

    def run_task(self, task, on_result, message):
        self.remember_view()
        self.activated.emit()
        self.info.setText(message)
        self.task = task
        task.result.connect(on_result)
        task.error.connect(self.analysis_error)
        task.finished.connect(self.analysis_done)
        self.controls()
        task.start()

    def analysis_error(self, text):
        self.info.setText("分析失败：" + text)

    def analysis_done(self):
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
        preferred = self.settings.value('analysis/view', '', type=str)
        restore_tab(self.views, preferred)
        self.controls()

    def cleanup_preview(self):
        if self.preview is not None:
            self.preview.cleanup()
            self.preview = None
