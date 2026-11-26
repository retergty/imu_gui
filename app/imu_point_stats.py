"""用加速度计三轴去均值后的合成 RMS 比较各点振动大小，同时保留分轴统计。"""
import argparse
import csv
import math
from collections import defaultdict
from dataclasses import dataclass, field
from operator import itemgetter
from pathlib import Path

from imu_schema import AXES, MOTORS, RPM_FIELDS

METRICS = ('mean', 'ac_rms')
# Main motor: one figure per axis; tail motor retains the combined view.
AXIS_PLOTS = {
    'main_rpm': {f'main_rpm_{a}_mean_rms.png': (a,) for a in AXES},
    'tail_rpm': {'tail_rpm_mean_rms.png': AXES},
}


@dataclass
class Moments:
    count: int = 0
    mean: list = field(default_factory=lambda: [0.0] * 3)
    centered_sum_squares: list = field(default_factory=lambda: [0.0] * 3)

    def add(self, values):
        self.count += 1
        for i, value in enumerate(values):
            delta = value - self.mean[i]
            self.mean[i] += delta / self.count
            self.centered_sum_squares[i] += delta * (value - self.mean[i])  # Welford：相对本组均值的平方偏差和

    def metrics(self, baseline=None):
        delta = [v - b for v, b in zip(self.mean, baseline.mean)] if baseline else [None] * len(AXES)
        return {"mean": self.mean, "mean_minus_baseline": delta, "ac_rms": [math.sqrt(s / self.count) for s in self.centered_sum_squares]}


class WaveformSummary:
    """Streaming statistics shared by CSV input and the VIB2 decoder."""

    def __init__(self):
        self.groups = defaultdict(Moments)
        self.speeds = {}

    def add_sample(self, point, source, values):
        if not all(map(math.isfinite, values)):
            raise ValueError('三轴数据中有 NaN 或无穷大')
        self.groups[point, source].add(values)

    def add_speed(self, point, source, count, values):
        if count < 0 or (point, source) in self.speeds:
            raise ValueError('RPM 样本数为负或测点转速汇总重复')
        if count and not all(map(math.isfinite, values)):
            raise ValueError('RPM 中有 NaN 或无穷大')
        self.speeds[point, source] = (*values, count) if count else (None, None, None, 0)

    def result(self):
        if not self.groups:
            raise ValueError('CSV 中没有采样数据')
        return self.groups, self.speeds


def summarize(path):
    summary = WaveformSummary()
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream, strict=True)
        columns = {name: i for i, name in enumerate(next(reader, ()))}
        missing = {'point', 'source', *AXES, *RPM_FIELDS}.difference(columns)
        if missing:
            raise ValueError('CSV 缺少列：' + ', '.join(sorted(missing)))
        source_point = itemgetter(columns['source'], columns['point'])
        axes = itemgetter(*(columns[k] for k in AXES))
        rpm = itemgetter(*(columns[k] for k in RPM_FIELDS[:3]))
        count_column = columns['rpm_count']
        for row in reader:
            if not row:
                continue
            try:
                source, point_text = source_point(row)
                point = int(point_text)
                if source in MOTORS:
                    n = int(row[count_column])
                    summary.add_speed(point, source, n, tuple(map(float, rpm(row))) if n > 0 else ())
                elif source in ('accel', 'gyro'):
                    summary.add_sample(point, source, tuple(map(float, axes(row))))
                else:
                    raise ValueError(f'未知 source: {source!r}')
            except (IndexError, ValueError) as exc:
                raise ValueError(f'CSV 第 {reader.line_num} 行：{exc}') from exc
    return summary.result()


def write_summary(path, groups, metrics_by_point):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['point', 'source', 'n'] + [f'{a}_{m}' for a in AXES for m in METRICS]
                        + [f'{a}_mean_minus_baseline' for a in AXES])
        for (point, source), group in sorted(groups.items()):
            metrics = metrics_by_point[point, source]
            writer.writerow([point, source, group.count] + [metrics[m][i] for i in range(3) for m in METRICS]
                            + metrics["mean_minus_baseline"])


def write_motor_table(folder, motor, speeds, levels):
    rows = [(p, *speeds.get((p, motor), (None,) * 4), levels.get(p))
            for p in set(levels) | {p for p, m in speeds if m == motor}]
    rows.sort(key=lambda r: (r[1] is None, r[1], r[0]))
    with (folder / f'{motor}.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['point', *RPM_FIELDS, 'accel_ac_rms_m_s2'])
        writer.writerows(rows)

    return rows


def generate_report(path, folder, figures=None, *, summary=None):
    """Render supplied streaming statistics, or compute them from the input CSV."""
    from imu_plot import plot_axes, save_vibration_curve

    table, image = folder / 'summary.csv', folder / 'mean_rms.png'
    motor_files = [folder / f'{m}{suffix}' for m in MOTORS for suffix in ('.csv', '.png', '_mean_rms.png')]
    axis_files = [folder / name for plots in AXIS_PLOTS.values() for name in plots]
    if path.resolve() in {p.resolve() for p in (table, image, *motor_files, *axis_files)}:
        raise ValueError('输出文件不能与输入日志相同')
    groups, speeds = summarize(path) if summary is None else summary.result()
    folder.mkdir(parents=True, exist_ok=True)
    metrics = {(point, source): group.metrics(groups.get((0, source)))
               for (point, source), group in groups.items()}
    write_summary(table, groups, metrics)
    levels = {p: math.hypot(*values['ac_rms'])
              for (p, source), values in metrics.items() if source == 'accel'}
    for motor in MOTORS:
        rows = write_motor_table(folder, motor, speeds, levels)
        active = save_vibration_curve(folder / f'{motor}.png', motor, rows, path.stem, figures)
        points = [(rpm, point) for point, rpm, *_ in rows if rpm is not None]
        for name, axes in AXIS_PLOTS[motor].items():
            if active:
                plot_axes(folder / name, [(None, metrics, points)], motor, axes, path.stem, figures)
            else:
                (folder / name).unlink(missing_ok=True)
    (folder / 'main_rpm_mean_rms.png').unlink(missing_ok=True)
    image.unlink(missing_ok=True)  # Remove the superseded point-indexed plot on regeneration.
    return groups, levels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_file', type=Path, help='包含 point, source, x, y, z 列的日志')
    parser.add_argument('-o', '--output-dir', type=Path, help='默认：输入文件旁的 <文件名>_stats 目录')
    args = parser.parse_args()
    path = args.csv_file
    folder = args.output_dir or path.parent / f'{path.stem}_stats'
    try:
        groups, levels = generate_report(path, folder)
    except ImportError as exc:
        parser.exit(1, f'无法加载绘图库：{exc}。请使用项目 .venv/bin/python -m pip install matplotlib\n')
    except (csv.Error, ValueError, OSError) as exc:
        parser.exit(1, f'{path}: {exc}\n')
    print(f"已统计 {sum(g.count for g in groups.values())} 条采样。\n{folder / 'summary.csv'}")
    if levels:
        maximum = max(levels.values())
        peaks = ', '.join(str(p) for p in sorted(levels) if levels[p] == maximum)
        print(f"最大振动：测点 {peaks}，合成 AC RMS = {maximum:.4f} m/s²。")
    else:
        print('没有加速度计采样。')
    for motor in MOTORS:
        print(folder / f'{motor}.csv')
        image = folder / f'{motor}.png'
        if image.exists():
            print(image, *(folder / name for name in AXIS_PLOTS[motor]), sep='\n')


if __name__ == '__main__':
    main()
