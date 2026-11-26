"""Build overlays on a shared measured-RPM axis from cached single-file reports."""
import csv
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp

from imu_point_stats import AXIS_PLOTS, METRICS
from imu_schema import AXES, MOTORS, REFERENCE_POINTS, SWEEP_POINTS
from imu_workbench import analyze


def merge_axis_tables(reports, folder):
    """Read only cached point statistics; keep each waveform's own baseline correction."""
    summaries = {}
    with (folder / 'summary.csv').open('w', encoding='utf-8', newline='') as output:
        writer = None
        for label, report in reports:
            table = report / 'summary.csv'
            if not table.exists():  # An empty capture has no statistics.
                continue
            metrics = {}
            with table.open(encoding='utf-8', newline='') as stream:
                reader = csv.DictReader(stream)
                if writer is None:
                    writer = csv.DictWriter(output, fieldnames=['waveform', *reader.fieldnames])
                    writer.writeheader()
                for row in reader:
                    writer.writerow({'waveform': label, **row})
                    metrics[int(row['point']), row['source']] = {
                        m: tuple(float(row[f'{a}_{m}']) if row[f'{a}_{m}'] else None for a in AXES)
                        for m in (*METRICS, 'mean_minus_baseline')}
            summaries[label] = metrics
    if not summaries:
        (folder / 'summary.csv').unlink()
    return summaries


def read_motor_points(table):
    rpm_points = []
    points = []
    if table.exists():
        with table.open(encoding='utf-8', newline='') as stream:
            for row in csv.DictReader(stream):
                if row['rpm_mean']:
                    rpm, point = float(row['rpm_mean']), int(row['point'])
                    rpm_points.append((rpm, point))
                    if row['accel_ac_rms_m_s2']:
                        points.append((rpm, point, float(row['accel_ac_rms_m_s2'])))
    return sorted(rpm_points), sorted(points)


def overlay(reports, folder, figures=None):
    from imu_plot import plot_axes, report_figure, save_figure
    from matplotlib.font_manager import get_font_names

    available = set(get_font_names())
    common_fonts = ('Microsoft YaHei', 'Microsoft YaHei UI', 'SimHei', 'SimSun',
                    'Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'PingFang SC')
    legend_font = next((name for name in common_fonts if name in available), None)
    labeled = [(f'{i}. {source.name}', report) for i, (source, report) in enumerate(reports.items(), 1)]
    summaries = merge_axis_tables(labeled, folder)
    missing = []
    for motor, title in MOTORS.items():
        fig = report_figure((10, 4.5))
        ax = fig.subplots()
        rows = []
        axis_datasets = []
        for label, report in labeled:
            table = report / f'{motor}.csv'
            rpm_points, points = read_motor_points(table)
            if label in summaries and any(rpm != 0 and p in SWEEP_POINTS for rpm, p in rpm_points):
                axis_datasets.append((label, summaries[label], rpm_points))
            sweep = [(rpm, level) for rpm, p, level in points if p in SWEEP_POINTS]
            if not any(rpm != 0 for rpm, _ in sweep):
                missing.append(f'{label} ({title})')
                continue
            line, = ax.plot(*zip(*sweep), 'o-', label=label)
            refs = [(rpm, level) for rpm, p, level in points if p in REFERENCE_POINTS]
            if refs:
                ax.scatter(*zip(*refs), marker='x', color=line.get_color(), gid=label)
            rows.extend((label, p, rpm, level) for rpm, p, level in points)
        if rows:
            # dataLim includes every waveform's sweep and its stopped references.
            # Fit after the last series, without cropping to shared RPMs or extrapolating.
            ax.margins(x=.05, y=.08, tight=True)
            ax.autoscale_view()
            ax.set_ylim(bottom=0)
            ax.set(title=title, xlabel='Measured mean speed (RPM)', ylabel='3-axis AC RMS (m/s²)')
            ax.grid(alpha=.25)
            ax.legend(loc='upper left', bbox_to_anchor=(1, 1),
                      prop={'family': legend_font} if legend_font else None)
            save_figure(fig, folder / f'{motor}.png', figures)
            with (folder / f'{motor}.csv').open('w', encoding='utf-8', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['waveform', 'point', 'rpm_mean', 'accel_ac_rms_m_s2'])
                writer.writerows(rows)
        if axis_datasets:
            for name, axes in AXIS_PLOTS[motor].items():
                plot_axes(folder / name, axis_datasets, motor, axes, 'Waveform comparison', figures,
                          legend_font=legend_font)
    (folder / 'sources.txt').write_text('\n'.join(f'{i}. {p}' for i, p in enumerate(reports, 1)), encoding='utf-8')
    return f'{len(reports)} 份波形，按实测 RPM 对齐。' + (' 无有效扫速数据：' + '、'.join(missing) if missing else '')


def append_reports(existing, paths, workspace, figures=None):
    if not paths:
        raise ValueError('请选择至少一份波形')
    reports = existing.copy()
    created = []
    try:
        for path in paths:
            try:
                report, message = analyze(path, workspace, figures if not existing and len(paths) == 1 else None)
            except Exception as exc:
                raise ValueError(f'{path.name}：{exc}') from exc
            created.append(report)
            reports[path] = report
        if len(reports) > 1:
            report = Path(mkdtemp(prefix='rpm_compare_', dir=workspace))
            created.append(report)
            message = overlay(reports, report, figures)
        return (report, reports), message
    except Exception:
        for folder in created:
            rmtree(folder, ignore_errors=True)
        raise
