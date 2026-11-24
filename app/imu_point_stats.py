"""用加速度计三轴去均值后的合成 RMS 比较各点振动大小，同时保留分轴统计。"""
import argparse
import csv
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

AXES = ('x', 'y', 'z')
SENSORS = (('accel', 'Accelerometer', 'm/s²'), ('gyro', 'Gyroscope', 'rad/s'))
METRICS = ("mean", "ac_rms")
MOTORS = {'main_rpm': 'Main motor', 'tail_rpm': 'Tail motor'}
RPM_FIELDS = ('rpm_mean', 'rpm_min', 'rpm_max', 'rpm_count')


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

    def metrics(self):
        return {"mean": self.mean, "ac_rms": [math.sqrt(s / self.count) for s in self.centered_sum_squares]}


def summarize(path):
    groups = defaultdict(Moments)
    speeds = {}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        missing = {'point', 'source', *AXES, *RPM_FIELDS}.difference(reader.fieldnames or ())
        if missing:
            raise ValueError('CSV 缺少列：' + ', '.join(sorted(missing)))
        for row in reader:
            try:
                source = row['source']
                point = int(row['point'])
                if source in MOTORS:
                    n = int(row['rpm_count'])
                    if n < 0 or (point, source) in speeds:
                        raise ValueError('RPM 样本数为负或测点转速汇总重复')
                    values = tuple(float(row[k]) for k in RPM_FIELDS[:3]) if n else (None,) * 3
                    if n and not all(map(math.isfinite, values)):
                        raise ValueError('RPM 中有 NaN 或无穷大')
                    speeds[point, source] = (*values, n)
                    continue
                if source not in ('accel', 'gyro'):
                    raise ValueError(f'未知 source: {source!r}')
                values = [float(row[a]) for a in AXES]
                if not all(map(math.isfinite, values)):
                    raise ValueError('三轴数据中有 NaN 或无穷大')
            except (TypeError, ValueError) as exc:
                raise ValueError(f'CSV 第 {reader.line_num} 行：{exc}') from exc
            groups[point, source].add(values)
    if not groups:
        raise ValueError('CSV 中没有采样数据')
    return groups, speeds


def write_summary(path, groups):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['point', 'source', 'n'] + [f'{a}_{m}' for a in AXES for m in METRICS])
        for (point, source), group in sorted(groups.items()):
            metrics = group.metrics()
            writer.writerow([point, source, group.count] + [metrics[m][i] for i in range(3) for m in METRICS])


def write_vibration_report(folder, motor, speeds, levels, title, figures=None):
    from imu_plot import report_figure, save_figure

    rows = [(p, *speeds.get((p, motor), (None,) * 4), levels.get(p))
            for p in sorted(set(levels) | {p for p, m in speeds if m == motor})]
    rows.sort(key=lambda r: (r[1] is None, r[1], r[0]))
    with (folder / f'{motor}.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['point', *RPM_FIELDS, 'accel_ac_rms_m_s2'])
        writer.writerows(rows)

    image = folder / f'{motor}.png'
    if not any(1 <= r[0] <= 20 and r[1] not in (None, 0) for r in rows):
        image.unlink(missing_ok=True)  # CLI runs may reuse an earlier report directory.
        return False
    fig = report_figure((10, 4.5))
    ax = fig.subplots()
    valid = [r for r in rows if r[1] is not None and r[-1] is not None]
    # Stopped references (0, 21) are not steps in the motor sweep.
    sweep = [r for r in valid if 1 <= r[0] <= 20]
    refs = [r for r in valid if r[0] in (0, 21)]
    if sweep:
        ax.plot([r[1] for r in sweep], [r[-1] for r in sweep], 'o-', label='Sweep')
    if refs:
        ax.scatter([r[1] for r in refs], [r[-1] for r in refs], marker='x', color='#b55424', label='Stopped references')
    if valid:
        ax.legend()
    else:
        ax.text(.5, .5, 'No paired RPM and acceleration data', transform=ax.transAxes, ha='center')
    ax.set(title=f'{MOTORS[motor]} | IMU vibration | {title}', xlabel='Measured mean speed (RPM)',
           ylabel='3-axis AC RMS (m/s²)', ylim=(0, None))
    ax.grid(axis='y', alpha=.25)
    save_figure(fig, image, figures)
    return True


def plot(path, groups, speeds, motor, title, figures=None):
    from imu_plot import report_figure, save_figure

    points = sorted((rpm[0], p) for (p, m), rpm in speeds.items() if m == motor and rpm[0] is not None)
    labels = {'mean': 'Mean', 'ac_rms': 'AC RMS (mean removed)'}
    fig = report_figure((12, 7))
    panels = fig.subplots(2, 2)
    for row, (source, sensor, unit) in enumerate(SENSORS):
        stats = {p: g.metrics() for (p, s), g in groups.items() if s == source}
        for panel, metric in zip(panels[row], METRICS):
            for i, axis in enumerate(AXES):
                sweep = [(rpm, stats[p][metric][i] if p in stats else math.nan) for rpm, p in points if 1 <= p <= 20]
                line, = panel.plot([x for x, _ in sweep], [y for _, y in sweep], marker=('o', 's', '^')[i], label=axis.upper())
                refs = [(rpm, stats[p][metric][i]) for rpm, p in points if p in (0, 21) and p in stats]
                panel.scatter([x for x, _ in refs], [y for _, y in refs], marker='x', color=line.get_color(), gid=axis.upper())
            panel.set(title=f'{sensor} | {labels[metric]}', xlabel='Measured mean speed (RPM)',
                      ylabel=unit)
            if metric != 'mean':
                panel.set_ylim(bottom=0)
            panel.grid(axis='y', alpha=.25)
            if stats:
                panel.legend(ncols=3)
            else:
                panel.text(.5, .5, 'No samples', transform=panel.transAxes, ha='center')
    fig.suptitle(f'{MOTORS[motor]} | IMU axis statistics — {title}')
    save_figure(fig, path, figures)


def generate_report(path, folder, figures=None):
    table, image = folder / 'summary.csv', folder / 'mean_rms.png'
    motor_files = [folder / f'{m}{suffix}' for m in MOTORS for suffix in ('.csv', '.png', '_mean_rms.png')]
    if path.resolve() in {p.resolve() for p in (table, image, *motor_files)}:
        raise ValueError('输出文件不能与输入日志相同')
    groups, speeds = summarize(path)
    folder.mkdir(parents=True, exist_ok=True)
    write_summary(table, groups)
    levels = {p: math.hypot(*group.metrics()['ac_rms'])
              for (p, source), group in groups.items() if source == 'accel'}
    for motor in MOTORS:
        axes_image = folder / f'{motor}_mean_rms.png'
        if write_vibration_report(folder, motor, speeds, levels, path.stem, figures):
            plot(axes_image, groups, speeds, motor, path.stem, figures)
        else:
            axes_image.unlink(missing_ok=True)
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
            print(image, folder / f'{motor}_mean_rms.png', sep='\n')


if __name__ == '__main__':
    main()
