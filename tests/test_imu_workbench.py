import csv
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QPoint, QSettings, Qt, QTimer
from PySide6.QtTest import QTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from imu_analysis_panel import CurvePreview
from imu_gui import Window
from imu_shell_transport import send_shell
from imu_ui import last_dir
from imu_vibration_download import download_file
from imu_workbench import analyze
from PySide6.QtWidgets import QApplication


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for_analysis(self, panel):
        if panel.task is not None:
            loop = QEventLoop()
            panel.task.finished.connect(loop.quit)
            QTimer.singleShot(30000, loop, loop.quit)
            loop.exec()
            self.app.processEvents()
        self.assertIsNone(panel.task, panel.info.text())

    def test_curve_preview_fits_available_width_and_height(self):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        fig = Figure(figsize=(4, 2), dpi=100)
        FigureCanvasAgg(fig).draw()
        preview = CurvePreview(fig)
        preview.show()
        for width, height, expected in [(1200, 800, (1200, 600)), (700, 200, (400, 200)), (200, 160, (200, 100))]:
            preview.resize(width, height)
            self.app.processEvents()
            self.assertEqual(preview.pixmap().size().toTuple(), expected)
        preview.close()

    def click_legend(self, preview, ax, entry, swatch=False):
        legend = ax.get_legend()
        text = legend.get_texts()[entry]
        renderer = preview.figure.canvas.get_renderer()
        box = text.get_window_extent(renderer)
        x = (box.x0 + box.x1) / 2
        if swatch:
            x = box.x0 - renderer.points_to_pixels(text.get_fontsize()) * (legend.handletextpad + legend.handlelength / 2)
        size = preview.pixmap().size()
        y = (box.y0 + box.y1) / 2
        point = QPoint(round((preview.width() - size.width()) / 2 + x * size.width() / preview.original.width()),
                       round((preview.height() - size.height()) / 2 + size.height() * (1 - y / preview.original.height())))
        QTest.mouseClick(preview, Qt.MouseButton.LeftButton, pos=point)

    def test_menu_order_and_old_selection(self):
        with TemporaryDirectory() as d:
            prefs = QSettings(str(Path(d) / 'prefs.ini'), QSettings.Format.IniFormat)
            w = Window(prefs)
            self.assertEqual([w.tabs.tabText(i) for i in range(w.tabs.count())],
                             ['IMU 振动测试', '飞控日志', '数据分析', 'NSH 控制台'])
            self.assertIs(w.tabs.currentWidget(), w.vibration)
            w.close()
            prefs.remove('ui/tabName')
            prefs.setValue('ui/tab', 1)  # NSH in the previous layout
            w = Window(prefs)
            self.assertEqual(w.tabs.currentIndex(), 3)
            w.close()

    def test_overlay_fits_disjoint_rpm_ranges_after_append(self):
        from imu_compare import overlay
        from matplotlib.figure import Figure

        with TemporaryDirectory() as d:
            root = Path(d)
            reports = {}
            for i, points in enumerate([[(1, 1700, 1), (2, 1900, 2)],
                                        [(1, 3200, 30)], [(0, 0, 45), (1, 600, 3)]]):
                report = root / str(i)
                report.mkdir()
                with (report / 'main_rpm.csv').open('w', newline='') as stream:
                    writer = csv.writer(stream)
                    writer.writerow(['point', 'rpm_mean', 'accel_ac_rms_m_s2'])
                    writer.writerows(points)
                reports[root / f'{i}.bin'] = report
                with patch.object(Figure, 'savefig', autospec=True) as save:
                    overlay(reports, root)
                    ax = save.call_args.args[0].axes[0]
                self.assertEqual([list(line.get_xdata()) for line in ax.lines],
                                 [[1700, 1900], [3200], [600]][:i + 1])
                self.assertLess(ax.get_xlim()[0], [1700, 1700, 0][i])
                self.assertGreater(ax.get_xlim()[1], [1900, 3200, 3200][i])
                self.assertGreater(ax.get_ylim()[1], [2, 30, 45][i])

    def test_compare_waveforms_in_gui(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            paths = [root / 'first.csv', root / 'second.csv']
            for path, offsets in zip(paths, [[0, 0], [1, 300]]):
                shift, rpm_offset = offsets
                with path.open('w', newline='') as stream:
                    out = csv.writer(stream)
                    out.writerow(['source', 'point', 'x', 'y', 'z', 'rpm_mean', 'rpm_min', 'rpm_max', 'rpm_count'])
                    for p, rpm in [(0, 0), (2, 2000 + rpm_offset), (3, 1500)]:
                        for x in [-1, 1]:
                            out.writerow(['accel', p + shift if p else 0, 10 + 50 * shift + p + x, 20 - p + x, 30 + 2*p + x, '', '', '', ''])
                            if p or shift:  # Only the second waveform has a gyro baseline.
                                out.writerow(['gyro', p + shift if p else 0, x, x, x, '', '', '', ''])
                        out.writerow(['main_rpm', p + shift if p else 0, '', '', '', rpm, rpm, rpm, 1])
            prefs = QSettings(str(root / 'prefs.ini'), QSettings.Format.IniFormat)
            prefs.setValue('ui/tabName', '本地分析')
            w = Window(prefs)
            self.assertEqual(w.tabs.tabText(w.tabs.currentIndex()), '数据分析')
            def append(files):
                with patch('imu_analysis_panel.QFileDialog.getOpenFileNames', return_value=(list(map(str, files)), '')):
                    w.analysis.choose.click()
                self.wait_for_analysis(w.analysis)
            w.show()
            append(paths[:1])
            for tab, (axis, deltas) in enumerate(zip('XYZ', ([3, 2], [-3, -2], [6, 4])), 1):
                self.assertEqual(w.analysis.views.tabText(tab), f'主电机 {axis} 轴统计')
                w.analysis.views.setCurrentIndex(tab)
                preview = w.analysis.views.currentWidget()
                mean, rms, gyro_mean, gyro_rms = preview.figure.axes
                self.assertEqual([line.get_label() for line in mean.lines], [axis])
                self.assertEqual(list(mean.lines[0].get_ydata()), deltas)
                self.assertEqual(list(mean.collections[0].get_offsets()[:, 1]), [0])
                self.assertEqual(list(rms.lines[0].get_ydata()), [1, 1])
                self.assertFalse(gyro_mean.lines)
                self.assertIn('No baseline', gyro_mean.texts[0].get_text())
                self.assertEqual(list(gyro_rms.lines[0].get_ydata()), [1, 1])
            self.click_legend(preview, mean, 0)
            self.assertFalse(mean.lines[0].get_visible())
            self.assertFalse(mean.collections[0].get_visible())
            self.assertTrue(rms.lines[0].get_visible())
            self.click_legend(preview, mean, 0, swatch=True)
            self.assertTrue(mean.collections[0].get_visible())
            with (w.analysis.report / 'summary.csv').open() as stream:
                rows = {(r['point'], r['source']): r for r in csv.DictReader(stream)}
            self.assertEqual(float(rows['2', 'accel']['z_mean']), 34)
            self.assertEqual(float(rows['2', 'accel']['z_mean_minus_baseline']), 4)
            self.assertEqual(rows['2', 'gyro']['z_mean_minus_baseline'], '')
            append(paths)
            append(paths)
            self.assertEqual(list(w.analysis.waveforms), paths)
            self.assertEqual(w.analysis.views.tabText(0), '主电机曲线', w.analysis.info.text())
            for tab, (axis, deltas) in enumerate(zip('XYZ', ([3, 2], [-3, -2], [6, 4])), 1):
                self.assertEqual(w.analysis.views.tabText(tab), f'主电机 {axis} 轴统计')
                mean, rms, gyro_mean, gyro_rms = w.analysis.views.widget(tab).figure.axes
                self.assertEqual([line.get_label() for line in mean.lines], ['1. first.csv', '2. second.csv'])
                self.assertEqual([list(line.get_xdata()) for line in mean.lines], [[1500, 2000], [1500, 2300]])
                self.assertEqual([list(line.get_ydata()) for line in mean.lines], [deltas, deltas])
                self.assertEqual([line.get_label() for line in gyro_mean.lines], ['2. second.csv'])
                self.assertEqual(len(gyro_rms.lines), 2)
            with (w.analysis.report / 'summary.csv').open() as stream:
                self.assertEqual({r['waveform'] for r in csv.DictReader(stream)}, {'1. first.csv', '2. second.csv'})
            w.analysis.views.setCurrentIndex(0)
            preview = w.analysis.views.currentWidget()
            ax = preview.figure.axes[0]
            limits = ax.get_xlim(), ax.get_ylim()
            original_png = (w.analysis.report / 'main_rpm.png').read_bytes()
            self.click_legend(preview, ax, 0, swatch=True)
            self.assertFalse(ax.lines[0].get_visible())
            self.assertFalse(ax.collections[0].get_visible())
            self.assertTrue(ax.lines[1].get_visible())
            self.assertTrue(ax.collections[1].get_visible())
            self.assertEqual(limits, (ax.get_xlim(), ax.get_ylim()))
            with (w.analysis.report / 'main_rpm.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([float(r['rpm_mean']) for r in rows], [0, 1500, 2000, 0, 1500, 2300])
            self.assertEqual([int(r['point']) for r in rows], [0, 3, 2, 0, 4, 3])
            old = w.analysis.report
            append([root / 'missing.csv'])
            self.assertEqual(w.analysis.report, old)
            with patch('imu_analysis_panel.QFileDialog.getExistingDirectory', return_value=d):
                w.analysis.save_report()
            saved = w.analysis.saved_report
            w.analysis.clear_btn.click()
            self.assertEqual(w.analysis.views.count(), 0)
            self.assertFalse(w.analysis.waveforms)
            self.assertFalse(old.exists())
            self.assertEqual((saved / 'main_rpm.png').read_bytes(), original_png)
            for axis in 'xyz':
                self.assertTrue((saved / f'main_rpm_{axis}_mean_rms.png').is_file())
            self.assertTrue(all(p.exists() for p in paths))
            w.close()

    def test_existing_waveform_in_gui(self):
        with TemporaryDirectory() as d:
            w = Window(QSettings(str(Path(d) / "prefs.ini"), QSettings.Format.IniFormat))
            with patch('imu_analysis_panel.QFileDialog.getOpenFileNames', return_value=([str(ROOT / 'examples/rpm_compare/A_mid.bin')], '')), \
                 patch('imu_analysis_panel.QFileDialog.getExistingDirectory') as select_dir:
                w.analysis.open_local()
                select_dir.assert_not_called()
            self.wait_for_analysis(w.analysis)
            self.assertEqual(w.analysis.views.count(), 7, w.analysis.info.text())
            for axis in "xyz":
                self.assertTrue((w.analysis.report / f"main_rpm_{axis}_mean_rms.png").is_file())
            self.assertFalse((w.analysis.report / "main_rpm_mean_rms.png").exists())
            self.assertFalse((w.analysis.report / "mean_rms.png").exists())
            self.assertFalse((w.analysis.report / "tail_rpm_mean_rms.png").exists())
            self.assertTrue((w.analysis.report / 'main_rpm.png').is_file())
            self.assertFalse((w.analysis.report / 'tail_rpm.png').exists())
            expected = {}
            with (w.analysis.report / 'samples.csv').open() as stream:
                for r in csv.DictReader(stream):
                    if r['source'] in ('main_rpm', 'tail_rpm'):
                        expected[r['source'], r['point']] = r['rpm_mean']
            for motor in ('main_rpm', 'tail_rpm'):
                with (w.analysis.report / f'{motor}.csv').open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), 4)
                rpms = [float(r['rpm_mean']) for r in rows if r['rpm_mean']]
                self.assertEqual(rpms, sorted(rpms))
                for r in rows:
                    self.assertEqual(r['rpm_mean'], expected.get((motor, r['point']), ''))
                    self.assertGreater(float(r['accel_ac_rms_m_s2']), 0)
            preview = w.analysis.report
            self.assertFalse(w.analysis.open_report.isEnabled())
            with patch('imu_analysis_panel.QFileDialog.getExistingDirectory', return_value=d):
                w.analysis.save_report_btn.click()
            saved = w.analysis.saved_report
            self.assertEqual(saved.parent, Path(d))
            self.assertEqual((saved / 'summary.csv').read_bytes(), (preview / 'summary.csv').read_bytes())
            w.auto.setChecked(True)
            w.close()
            self.assertFalse(preview.exists())
            self.assertTrue(saved.exists())
            restored = Window(QSettings(str(Path(d) / 'prefs.ini'), QSettings.Format.IniFormat))
            self.assertEqual(restored.tabs.currentIndex(), 2)
            self.assertTrue(restored.auto.isChecked())
            self.assertEqual(last_dir(restored.settings, 'analysis/saveDir'), Path(d))
            self.assertEqual(last_dir(restored.settings, 'analysis/openDir'), ROOT / 'examples/rpm_compare')
            restored.close()

    def test_restore_connection_without_opening_port(self):
        with TemporaryDirectory() as d:
            path = str(Path(d) / "prefs.ini")
            w = Window(QSettings(path, QSettings.Format.IniFormat))
            w.device.setCurrentText("COM42")
            w.baud.setCurrentText("460800")
            w.close()
            restored = Window(QSettings(path, QSettings.Format.IniFormat))
            self.assertEqual(restored.device.currentText(), "COM42")
            self.assertEqual(restored.baud.currentText(), "460800")
            self.assertIsNone(restored.link)
            restored.close()

    def test_restore_vibration_choices_without_starting(self):
        with TemporaryDirectory() as d:
            path = str(Path(d) / "prefs.ini")
            w = Window(QSettings(path, QSettings.Format.IniFormat))
            panel = w.vibration
            panel.mode.setCurrentIndex(2)
            panel.first.setValue(4)
            panel.last.setValue(7)
            panel.save.setChecked(True)
            panel.name.setText("bench.bin")
            w.close()
            restored = Window(QSettings(path, QSettings.Format.IniFormat))
            panel = restored.vibration
            self.assertEqual((panel.mode.currentIndex(), panel.first.value(), panel.last.value()), (2, 4, 7))
            self.assertTrue(panel.save.isChecked())
            self.assertEqual(panel.name.text(), "bench.bin")
            self.assertFalse(panel.begin.isEnabled())
            restored.close()

    def test_invalid_waveform_and_ulog(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "wave.ulg"
            for data in (b"VIB1bad", b"ULog"):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    analyze(path, path.parent)
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_download_does_not_replace_on_truncation(self):
        ftp = Mock()
        ok = SimpleNamespace(error_code=0)

        def get(args, **kw):
            Path(args[1]).write_bytes(b"x")
            return ok

        ftp.cmd_get.side_effect = get
        ftp.process_ftp_reply.return_value = ok
        with TemporaryDirectory() as d:
            path = Path(d) / "log.bin"
            path.write_bytes(b"original")
            with self.assertRaises(OSError):
                download_file(
                    ftp, SimpleNamespace(name="wave", size_b=3), path, lambda *_: None
                )
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_shell_chunking_and_release(self):
        link = Mock()
        send_shell(link, b"x" * 72)
        send_shell(link, b"")
        calls = link.mav.serial_control_send.call_args_list
        self.assertEqual([c.args[4] for c in calls], [70, 2, 0])
        self.assertEqual(calls[-1].args[1], 0)
