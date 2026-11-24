import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtCore import QEventLoop, QPoint, QSettings, Qt, QTimer
from PySide6.QtTest import QTest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from imu_gui import QApplication, CurvePreview, Window
from imu_shell_transport import send_shell
from imu_workbench import analyze
from imu_vibration_download import download_file



class WorkbenchTests(unittest.TestCase):
    def test_curve_preview_fits_available_width_and_height(self):
        app = QApplication.instance() or QApplication([])
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        fig = Figure(figsize=(4, 2), dpi=100)
        FigureCanvasAgg(fig).draw()
        preview = CurvePreview(fig)
        preview.show()
        for width, height, expected in [(1200, 800, (1200, 600)), (700, 200, (400, 200)), (200, 160, (200, 100))]:
            preview.resize(width, height)
            app.processEvents()
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
        app = QApplication.instance() or QApplication([])
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
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as d:
            root = Path(d)
            paths = [root / 'first.csv', root / 'second.csv']
            for path, offsets in zip(paths, [[0, 0], [1, 300]]):
                shift, rpm_offset = offsets
                with path.open('w', newline='') as stream:
                    out = csv.writer(stream)
                    out.writerow('source point x y z rpm_mean rpm_min rpm_max rpm_count'.split())
                    for p, rpm in [(0, 0), (2, 2000 + rpm_offset), (3, 1500)]:
                        for x in [-1, 1]:
                            out.writerow(['accel', p + shift if p else 0, x, 0, 0, '', '', '', ''])
                        out.writerow(['main_rpm', p + shift if p else 0, '', '', '', rpm, rpm, rpm, 1])
            prefs = QSettings(str(root / 'prefs.ini'), QSettings.Format.IniFormat)
            prefs.setValue('ui/tabName', '本地分析')
            w = Window(prefs)
            self.assertEqual(w.tabs.tabText(w.tabs.currentIndex()), '数据分析')
            def append(files):
                with patch('imu_gui.QFileDialog.getOpenFileNames', return_value=(list(map(str, files)), '')):
                    w.choose.click()
                if w.analysis:
                    loop = QEventLoop()
                    w.analysis.finished.connect(loop.quit)
                    QTimer.singleShot(30000, loop.quit)
                    loop.exec()
                    app.processEvents()
            w.show()
            append(paths[:1])
            w.views.setCurrentIndex(1)
            axes_preview = w.views.currentWidget()
            ax, other = axes_preview.figure.axes[:2]
            self.click_legend(axes_preview, ax, 1)
            self.assertEqual([line.get_visible() for line in ax.lines], [True, False, True])
            self.assertFalse(ax.collections[1].get_visible())
            self.assertTrue(other.lines[1].get_visible())
            self.click_legend(axes_preview, ax, 1, swatch=True)
            self.assertTrue(ax.collections[1].get_visible())
            self.assertTrue(ax.lines[1].get_visible())
            append(paths)
            append(paths)
            self.assertEqual(list(w.waveforms), paths)
            self.assertEqual(w.views.tabText(0), '主电机曲线', w.info.text())
            w.views.setCurrentIndex(0)
            preview = w.views.currentWidget()
            ax = preview.figure.axes[0]
            limits = ax.get_xlim(), ax.get_ylim()
            original_png = (w.report / 'main_rpm.png').read_bytes()
            self.click_legend(preview, ax, 0, swatch=True)
            self.assertFalse(ax.lines[0].get_visible())
            self.assertFalse(ax.collections[0].get_visible())
            self.assertTrue(ax.lines[1].get_visible())
            self.assertTrue(ax.collections[1].get_visible())
            self.assertEqual(limits, (ax.get_xlim(), ax.get_ylim()))
            with (w.report / 'main_rpm.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([float(r['rpm_mean']) for r in rows], [0, 1500, 2000, 0, 1500, 2300])
            self.assertEqual([int(r['point']) for r in rows], [0, 3, 2, 0, 4, 3])
            old = w.report
            append([root / 'missing.csv'])
            self.assertEqual(w.report, old)
            with patch('imu_gui.QFileDialog.getExistingDirectory', return_value=d):
                w.save_report()
            saved = w.saved_report
            w.clear_btn.click()
            self.assertEqual(w.views.count(), 0)
            self.assertFalse(w.waveforms)
            self.assertFalse(old.exists())
            self.assertEqual((saved / 'main_rpm.png').read_bytes(), original_png)
            self.assertTrue(all(p.exists() for p in paths))
            w.close()

    def test_existing_waveform_in_gui(self):
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as d:
            w = Window(QSettings(str(Path(d) / "prefs.ini"), QSettings.Format.IniFormat))
            with patch('imu_gui.QFileDialog.getOpenFileNames', return_value=([str(ROOT / 'logs/raw/imu01.bin')], '')), \
                 patch('imu_gui.QFileDialog.getExistingDirectory') as select_dir:
                w.open_local()
                select_dir.assert_not_called()
            loop = QEventLoop()
            w.analysis.finished.connect(loop.quit)
            QTimer.singleShot(30000, loop.quit)
            loop.exec()
            app.processEvents()
            self.assertIsNone(w.analysis)
            self.assertEqual(w.views.count(), 5, w.info.text())
            self.assertTrue((w.report / "main_rpm_mean_rms.png").is_file())
            self.assertFalse((w.report / "mean_rms.png").exists())
            self.assertFalse((w.report / "tail_rpm_mean_rms.png").exists())
            self.assertTrue((w.report / 'main_rpm.png').is_file())
            self.assertFalse((w.report / 'tail_rpm.png').exists())
            expected = {}
            with (w.report / 'samples.csv').open() as stream:
                for r in csv.DictReader(stream):
                    if r['source'] in ('main_rpm', 'tail_rpm'):
                        expected[r['source'], r['point']] = r['rpm_mean']
            for motor in ('main_rpm', 'tail_rpm'):
                with (w.report / f'{motor}.csv').open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), 20)
                rpms = [float(r['rpm_mean']) for r in rows if r['rpm_mean']]
                self.assertEqual(rpms, sorted(rpms))
                for r in rows:
                    self.assertEqual(r['rpm_mean'], expected[motor, r['point']])
                    self.assertGreater(float(r['accel_ac_rms_m_s2']), 0)
            preview = w.report
            self.assertFalse(w.open_report.isEnabled())
            with patch('imu_gui.QFileDialog.getExistingDirectory', return_value=d):
                w.save_report_btn.click()
            saved = w.saved_report
            self.assertEqual(saved.parent, Path(d))
            self.assertEqual((saved / 'summary.csv').read_bytes(), (preview / 'summary.csv').read_bytes())
            w.auto.setChecked(True)
            w.close()
            self.assertFalse(preview.exists())
            self.assertTrue(saved.exists())
            restored = Window(QSettings(str(Path(d) / 'prefs.ini'), QSettings.Format.IniFormat))
            self.assertEqual(restored.tabs.currentIndex(), 2)
            self.assertTrue(restored.auto.isChecked())
            self.assertEqual(restored.last_dir('analysis/saveDir'), Path(d))
            self.assertEqual(restored.last_dir('analysis/openDir'), ROOT / 'logs/raw')
            restored.close()

    def test_restore_connection_without_opening_port(self):
        app = QApplication.instance() or QApplication([])
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
        app = QApplication.instance() or QApplication([])
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
