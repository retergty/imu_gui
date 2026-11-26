import csv
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from imu_fft import amplitude_spectrum, read_point_samples, render_fft, welch_density, welch_segment_size


class SpectrumTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'samples.csv'

    def write(self, timestamps, xyz):
        with self.path.open('w', newline='') as f:
            out = csv.writer(f)
            out.writerow(['source', 'point', 'timestamp_sample_us', 'x', 'y', 'z'])
            for t, values in zip(timestamps, xyz):
                out.writerow(['accel', 2, t, *values])
                out.writerow(['gyro', 2, t, 100, 200, 300])
                out.writerow(['accel', 1, t, 10, 20, 30])

    def test_single_sided_peak_amplitudes_even_and_odd(self):
        for n in (100, 101):
            i = np.arange(n)
            xyz = np.stack([9 + 3 * np.cos(2*np.pi*10*i/n), (-1.)**i, np.ones(n)], axis=1)
            self.write(11000 + 1000*i, xyz)
            data, fs = read_point_samples(self.path, 2, 'accel')
            freq, amps = amplitude_spectrum(data, fs)
            self.assertEqual((fs, len(data)), (1000, n))
            self.assertAlmostEqual(freq[np.argmax(amps[:, 0])], fs*10/n)
            self.assertAlmostEqual(amps[10, 0], 3)
            self.assertAlmostEqual(amps[0, 0], 0)
            self.assertTrue(np.all(amps[:, 2] == 0))
            if n % 2 == 0:
                self.assertAlmostEqual(amps[-1, 1], 1)

    def test_magnitude_spectrum_uses_raw_vector_length(self):
        i = np.arange(100)
        # A rotating vector with a constant offset on Z has constant length,
        # even though both horizontal axes have a strong 100 Hz peak.
        phase = 2 * np.pi * i / 10
        self.write(1000 * i, np.column_stack((3 * np.cos(phase), 3 * np.sin(phase), np.full(100, 4))))
        _, amps = amplitude_spectrum(*read_point_samples(self.path, 2, 'accel'))
        self.assertEqual(amps.shape, (51, 4))
        self.assertAlmostEqual(amps[10, 0], 3)
        np.testing.assert_allclose(amps[:, 3], 0, atol=1e-12)
        # Fixed direction, varying length: the magnitude retains the modulation.
        radius = 4 + 2 * np.cos(phase)
        self.write(1000 * i, np.outer(radius, [1/3, 2/3, 2/3]))
        _, amps = amplitude_spectrum(*read_point_samples(self.path, 2, 'accel'))
        self.assertAlmostEqual(amps[10, 3], 2)
        self.assertAlmostEqual(amps[0, 3], 0)

    def test_welch_density_power_and_peak(self):
        n, fs = 1024, 1000
        phase = 2 * np.pi * np.arange(n) / 16
        # Include a DC offset and a Nyquist tone to check one-sided scaling.
        xyz = np.column_stack((5 + 2 * np.cos(phase), (-1.) ** np.arange(n), np.ones(n)))
        self.write(1000 * np.arange(n), xyz)
        data, fs = read_point_samples(self.path, 2, 'accel')
        length = welch_segment_size(n)
        psd = welch_density(data, fs, length)
        freq = np.fft.rfftfreq(length, 1 / fs)
        self.assertEqual(psd.shape, (129, 4))
        self.assertAlmostEqual(freq[np.argmax(psd[:, 0])], fs / 16)
        np.testing.assert_allclose(psd[:, :3].sum(axis=0) * freq[1], [2, 1, 0], atol=1e-12)

    def test_short_point_preserves_raw_fft(self):
        self.write(np.arange(11), [(1, 2, 3)] * 11)
        figures = {}
        _, message = render_fft(self.path, 2, 'accel', figures=figures)
        top, bottom = figures['fft'].axes
        self.assertEqual(len(top.lines), 4)
        self.assertFalse(bottom.lines)
        self.assertIn('Welch 至少需要 12 个采样', message)

    def test_time_validation(self):
        for stamps, message in [([1, 2], '至少'), ([1, 2, 2, 3], '重复'), ([1, 2, 6, 7], '缺口')]:
            self.write(stamps, [(1, 2, 3)] * len(stamps))
            with self.assertRaisesRegex(ValueError, message):
                read_point_samples(self.path, 2, 'accel')
