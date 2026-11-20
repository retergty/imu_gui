"""Build overlays on a shared measured-RPM axis from cached single-file reports."""
import csv
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp

from imu_point_stats import MOTORS
from imu_workbench import analyze


def overlay(reports, folder):
    from matplotlib.figure import Figure

    missing = []
    for motor, title in MOTORS.items():
        fig = Figure(figsize=(10, 4.5), constrained_layout=True)
        ax = fig.subplots()
        rows = []
        for i, (source, report) in enumerate(reports.items(), 1):
            label = f'{i}. {source.name}'
            table = report / f'{motor}.csv'
            points = []
            if table.exists():
                with table.open(encoding='utf-8', newline='') as stream:
                    points = sorted((float(r['rpm_mean']), int(r['point']), float(r['accel_ac_rms_m_s2']))
                                    for r in csv.DictReader(stream) if r['rpm_mean'] and r['accel_ac_rms_m_s2'])
            sweep = [(rpm, level) for rpm, p, level in points if 1 <= p <= 20]
            if not any(rpm != 0 for rpm, _ in sweep):
                missing.append(f'{label} ({title})')
                continue
            line, = ax.plot(*zip(*sweep), 'o-', label=label)
            refs = [(rpm, level) for rpm, p, level in points if p in (0, 21)]
            if refs:
                ax.scatter(*zip(*refs), marker='x', color=line.get_color())
            rows.extend((label, p, rpm, level) for rpm, p, level in points)
        if rows:
            # dataLim includes every waveform's sweep and its stopped references.
            # Fit after the last series, without cropping to shared RPMs or extrapolating.
            ax.margins(x=.05, y=.08, tight=True)
            ax.autoscale_view()
            ax.set_ylim(bottom=0)
            ax.set(title=title, xlabel='Measured mean speed (RPM)', ylabel='3-axis AC RMS (m/s²)')
            ax.grid(alpha=.25)
            ax.legend(loc='upper left', bbox_to_anchor=(1, 1))
            fig.savefig(folder / f'{motor}.png', dpi=150)
            with (folder / f'{motor}.csv').open('w', encoding='utf-8', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['waveform', 'point', 'rpm_mean', 'accel_ac_rms_m_s2'])
                writer.writerows(rows)
    (folder / 'sources.txt').write_text('\n'.join(f'{i}. {p}' for i, p in enumerate(reports, 1)), encoding='utf-8')
    return f'{len(reports)} 份波形，按实测 RPM 对齐。' + (' 无有效扫速数据：' + '、'.join(missing) if missing else '')


def append_reports(existing, paths, workspace):
    reports = existing.copy()
    created = []
    try:
        for path in paths:
            try:
                report, message = analyze(path, workspace)
            except Exception as exc:
                raise ValueError(f'{path.name}：{exc}') from exc
            created.append(report)
            reports[path] = report
        if len(reports) > 1:
            report = Path(mkdtemp(prefix='rpm_compare_', dir=workspace))
            created.append(report)
            message = overlay(reports, report)
        return (report, reports), message
    except Exception:
        for folder in created:
            rmtree(folder, ignore_errors=True)
        raise
