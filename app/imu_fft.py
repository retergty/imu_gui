"""On-demand frequency analysis of one sensor at one factory measurement point."""
import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from imu_compare import read_motor_points
from imu_schema import AXES, SENSORS


@dataclass
class FFTCurve:
    label: str
    samples: Path
    points: list  # (point id, measured RPM or None, available sensors)


def fft_curves(waveforms):
    """Populate selectors from the small per-file reports, never from sample streams."""
    curves = []
    for index, (source, report) in enumerate(waveforms.items(), 1):
        table = report / 'summary.csv'
        if not table.exists():
            continue
        sensors = defaultdict(list)
        with table.open(encoding='utf-8', newline='') as stream:
            for row in csv.DictReader(stream):
                sensors[int(row['point'])].append(row['source'])
        samples = report / 'samples.csv'
        if not samples.is_file():  # Direct CSV input is not copied into the report.
            samples = source
        label = f'{index}. {source.name}'
        covered = set()
        for motor, name in [('main_rpm', '主电机'), ('tail_rpm', '尾电机')]:
            rpms, _ = read_motor_points(report / f'{motor}.csv')
            points = [(p, rpm, sensors[p]) for rpm, p in rpms if p in sensors]
            if points:
                curves.append(FFTCurve(f'{label} · {name}', samples, points))
                covered.update(p for p, _, _ in points)
        missing = [(p, None, sensors[p]) for p in sorted(sensors.keys() - covered)]
        if missing:
            curves.append(FFTCurve(f'{label} · 无转速记录', samples, missing))
    return curves


WELCH_MIN_SAMPLES = 12


def welch_segment_size(n):
    # About seven half-overlapping segments, with at least eight samples each.
    return max(8, 2 * (n // 8))


def welch_density(data, fs, length):
    step = length // 2
    starts = range(0, len(data) - length + 1, step)
    window = np.hanning(length + 1)[:-1]
    density = np.zeros((length // 2 + 1, data.shape[1]))
    for start in starts:
        segment = data[start:start + length]
        centered = segment - segment.mean(axis=0)
        density += np.abs(np.fft.rfft(centered * window[:, None], axis=0)) ** 2
    density /= len(starts) * fs * (window @ window)
    density[1:(length + 1) // 2] *= 2
    return density


def read_point_samples(path, point, sensor):
    timestamps, values = [], []
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'point', 'source', 'timestamp_sample_us', *AXES} <= set(reader.fieldnames or ()):
            raise ValueError('FFT 需要含 timestamp_sample_us 和三轴原始采样的 CSV。')
        for row in reader:
            if row['source'] == sensor and int(row['point']) == point:
                try:
                    timestamps.append(int(row['timestamp_sample_us']))
                    values.append([float(row[a]) for a in AXES])
                except (ValueError, TypeError) as exc:
                    raise ValueError(f'CSV 第 {reader.line_num} 行的时间戳或三轴采样无效。') from exc
    n = len(values)
    if n < 4:
        raise ValueError('该测点至少需要 4 个连续采样。')
    t = np.asarray(timestamps, dtype=np.int64) - timestamps[0]
    dt = np.diff(t)
    if np.any(dt <= 0):
        raise ValueError('该测点时间戳重复或未递增。')
    if np.any(np.abs(dt / np.median(dt) - 1) > 0.2):
        raise ValueError('该测点采样间隔不均匀或存在缺口，未计算 FFT。')
    data = np.asarray(values)
    if not np.isfinite(data).all():
        raise ValueError('采样含非有限值，无法计算 FFT。')
    # Form the magnitude in the time domain before interpolation and DC removal.
    data = np.column_stack((data, np.hypot.reduce(data, axis=1)))
    # Correct small timing jitter before applying a periodic Hann window.
    grid = np.linspace(0, t[-1], n)
    data = np.column_stack([np.interp(grid, t, axis) for axis in data.T])
    fs = (n - 1) * 1e6 / t[-1]
    return data, fs


def amplitude_spectrum(data, fs):
    n = len(data)
    window = np.hanning(n + 1)[:-1]
    amplitudes = np.abs(np.fft.rfft((data - data.mean(axis=0)) * window[:, None], axis=0)) / window.sum()
    amplitudes[1:(n + 1) // 2] *= 2  # DC and an even-length Nyquist bin have no negative partner.
    return np.fft.rfftfreq(n, 1 / fs), amplitudes


def plot_spectrum(ax, freq, values, title, ylabel):
    for axis, data in zip(AXES, values[:, :3].T, strict=True):
        ax.plot(freq, data, label=axis.upper())
    ax.plot(freq, values[:, 3], '--', label='Magnitude')
    ax.set(title=title, ylabel=ylabel, ylim=(0, None))
    ax.grid(alpha=.25)
    ax.legend(ncols=4, fontsize='small')


def render_fft(path, point, sensor, *, figures):
    from matplotlib.figure import Figure

    data, fs = read_point_samples(path, point, sensor)
    n = len(data)
    figure = Figure(figsize=(9, 6), constrained_layout=True)
    top, bottom = figure.subplots(2, 1, sharex=True)
    _, name, unit = next(s for s in SENSORS if s[0] == sensor)
    figure.suptitle(f'{name} | Point {point}')
    freq, amplitudes = amplitude_spectrum(data, fs)
    plot_spectrum(top, freq, amplitudes, 'FFT', f'Peak amplitude ({unit})')
    top.tick_params(axis='x', labelbottom=True)
    top.set_xlabel('Frequency (Hz)')
    summary = f'{n} 个采样 · 采样率 {fs:.2f} Hz · FFT 频点间隔 {freq[1]:.3g} Hz'
    if n >= WELCH_MIN_SAMPLES:
        length = welch_segment_size(n)
        freq = np.fft.rfftfreq(length, 1 / fs)
        psd = welch_density(data, fs, length)
        plot_spectrum(bottom, freq, psd, 'Welch PSD', f'Power spectral density (({unit})²/Hz)')
        count = 1 + (n - length) // (length // 2)
        summary += f' · Welch 频点间隔 {freq[1]:.3g} Hz（{count} 段，每段 {length} 点，重叠 50%）'
    else:
        bottom.set_title('Welch PSD')
        bottom.text(.5, .5, f'At least {WELCH_MIN_SAMPLES} samples required', ha='center', va='center',
                    transform=bottom.transAxes)
        bottom.set_yticks([])
        summary += f' · Welch 至少需要 {WELCH_MIN_SAMPLES} 个采样，仅显示原始 FFT。'
    bottom.set(xlim=(0, fs / 2), xlabel='Frequency (Hz)')
    figures['fft'] = figure
    return None, summary
