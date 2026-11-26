"""Failure paths must preserve existing reports and never leave partial output."""
import csv
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from imu_compare import append_reports
from imu_point_stats import generate_report, summarize
from imu_workbench import analyze, copy_report


class ReportWorkflowTests(unittest.TestCase):
    def test_binary_report_matches_csv_without_rereading_export(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('imu_point_stats.summarize', side_effect=AssertionError('CSV reread')):
                report, _ = analyze(ROOT / 'examples/rpm_compare/A_mid.bin', root)
            other = root / 'from_csv'
            generate_report(report / 'samples.csv', other)
            self.assertEqual({p.name for p in report.iterdir()} - {'samples.csv'},
                             {p.name for p in other.iterdir()})
            for path in other.iterdir():
                self.assertEqual(path.read_bytes(), (report / path.name).read_bytes(), path.name)

    def test_streamed_summary_rejects_invalid_data_and_removes_partial_report(self):
        sample = struct.pack('<BBBBQfff', 0, 1, 5, 0, 1, 1.0, 2.0, 3.0)
        nan_sample = struct.pack('<BBBBQfff', 0, 1, 5, 0, 1, 1.0, float('nan'), 3.0)
        rpm = struct.pack('<BBHIfffI', 2, 1, 0, 1, 100.0, 90.0, 110.0, 0)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'bad.bin'
            for records, error in [([sample, rpm, rpm], '重复'), ([nan_sample], 'NaN'), ([rpm], '没有采样')]:
                with self.subTest(error=error):
                    path.write_bytes(b'VIB2' + b''.join(records) + struct.pack('<4sII', b'END2', 0, len(records)))
                    with self.assertRaisesRegex(ValueError, error):
                        analyze(path, root)
                    self.assertEqual(list(root.iterdir()), [path])

    def test_csv_column_lookup_supports_reordering_and_line_numbers(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'input.csv'
            contents = ('z,point,x,rpm_count,source,y,rpm_max,rpm_min,rpm_mean\n\n'
                        '3,0,1,,accel,2,,,\n,0,,0,main_rpm,,,,\n')
            path.write_text(contents, encoding='utf-8-sig')
            groups, speeds = summarize(path)
            self.assertEqual(groups[0, 'accel'].mean, [1, 2, 3])
            self.assertEqual(speeds[0, 'main_rpm'], (None, None, None, 0))
            path.write_text(contents + '3,0,1,,accel\n', encoding='utf-8-sig')
            with self.assertRaisesRegex(ValueError, 'CSV 第 5 行'):
                summarize(path)

    def test_append_rolls_back_only_new_reports(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cached = root / 'cached'
            cached.mkdir()
            existing = {root / 'old.csv': cached}
            empty = root / 'empty.bin'
            empty.write_bytes(b'VIB2END2' + bytes(8))
            broken = root / 'broken.bin'
            broken.write_bytes(b'VIB2unfinished')
            before = set(root.iterdir())
            with self.assertRaisesRegex(ValueError, 'broken.bin'):
                append_reports(existing, [empty, broken], root)
            self.assertEqual(set(root.iterdir()), before)
            self.assertEqual(list(existing.values()), [cached])

    def test_failed_export_preserves_source_and_destination(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / 'report'
            report.mkdir()
            sample = report / 'summary.csv'
            sample.write_bytes(b'original')
            with self.assertRaises(ValueError):
                copy_report(report, report / 'inside', sample)
            with patch('imu_workbench.copytree', side_effect=OSError('write failed')), self.assertRaises(OSError):
                copy_report(report, root, sample)
            self.assertEqual(list(root.iterdir()), [report])
            self.assertEqual(sample.read_bytes(), b'original')

    def test_regenerate_tail_report_and_remove_stale_main_images(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'samples.csv'
            source.write_text('source,point,x,y,z,rpm_mean,rpm_min,rpm_max,rpm_count\n'
                              'accel,1,2,4,6,,,,\naccel,1,4,6,8,,,,\ntail_rpm,1,,,,100,90,110,2\n')
            stale = ['main_rpm.png', 'main_rpm_x_mean_rms.png', 'main_rpm_mean_rms.png', 'mean_rms.png']
            for name in stale:
                (root / name).touch()
            figures = {}
            generate_report(source, root, figures)
            self.assertEqual(set(figures), {'tail_rpm.png', 'tail_rpm_mean_rms.png'})
            self.assertFalse(any((root / name).exists() for name in stale))
            axes = figures['tail_rpm_mean_rms.png'].axes
            self.assertEqual([line.get_ydata().item() for line in axes[0].lines], [3, 5, 7])
            with (root / 'summary.csv').open() as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(float(row['x_ac_rms']), 1)
            self.assertEqual(row['x_mean_minus_baseline'], '')

    def test_csv_export_publishes_only_complete_new_files(self):
        script = ROOT / '.agents/skills/imu-log-to-csv/scripts/export.py'
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'wave.bin'
            target = path.with_suffix('.csv')
            command = [sys.executable, script, path]
            path.write_bytes(b'VIB2bad')
            self.assertNotEqual(subprocess.run(command, cwd=root, capture_output=True, check=False).returncode, 0)
            self.assertEqual(list(root.iterdir()), [path])
            path.write_bytes(b'VIB2END2' + bytes(8))
            result = subprocess.run(command, cwd=root, capture_output=True, check=True)
            self.assertIn(b'success: 0 records', result.stderr)
            self.assertTrue(target.read_bytes().startswith(b'source,point,'))
            target.write_bytes(b'keep')
            self.assertNotEqual(subprocess.run(command, cwd=root, capture_output=True, check=False).returncode, 0)
            self.assertEqual(target.read_bytes(), b'keep')
