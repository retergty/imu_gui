"""Render the report image and optionally keep its Agg canvas for GUI interaction."""
import math

from imu_schema import AXES, MOTORS, REFERENCE_POINTS, SENSORS, SWEEP_POINTS
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


def report_figure(size):
    figure = Figure(figsize=size, dpi=150, constrained_layout=True)
    FigureCanvasAgg(figure)
    return figure


def save_figure(figure, path, figures):
    figure.savefig(path, dpi=figure.dpi)
    if figures is not None:
        figures[path.name] = figure


def save_vibration_curve(image, motor, rows, title, figures):
    if not any(r[0] in SWEEP_POINTS and r[1] not in (None, 0) for r in rows):
        image.unlink(missing_ok=True)  # CLI runs may reuse an earlier report directory.
        return False
    fig = report_figure((10, 4.5))
    ax = fig.subplots()
    valid = [r for r in rows if r[1] is not None and r[-1] is not None]
    # Stopped references (0, 21) are not steps in the motor sweep.
    sweep = [r for r in valid if r[0] in SWEEP_POINTS]
    refs = [r for r in valid if r[0] in REFERENCE_POINTS]
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


def plot_axes(path, datasets, motor, axes, title, figures=None, *, legend_font=None):
    """Plot (waveform label, metrics by point/sensor, measured RPM points) datasets."""
    mean = 'mean_minus_baseline' if motor == 'main_rpm' else 'mean'
    labels = {'mean': 'Mean', 'mean_minus_baseline': 'Mean minus baseline (point 0)', 'ac_rms': 'AC RMS (mean removed)'}
    fig = report_figure((12, 7))
    panels = fig.subplots(2, 2)
    for row, (source, sensor, unit) in enumerate(SENSORS):
        for panel, metric in zip(panels[row], (mean, 'ac_rms')):
            panel.set(title=f'{sensor} | {labels[metric]}', xlabel='Measured mean speed (RPM)',
                      ylabel=unit)
            panel.grid(axis='y', alpha=.25)
            missing_baseline = False
            for index, (waveform, metrics, points) in enumerate(datasets):
                stats = {p: values for (p, s), values in metrics.items() if s == source}
                if not stats:
                    continue
                if metric == 'mean_minus_baseline' and 0 not in stats:
                    missing_baseline = True
                    continue
                for axis in axes:
                    i = AXES.index(axis)
                    label = axis.upper() if not waveform else (
                        waveform if len(axes) == 1 else f'{waveform} | {axis.upper()}')
                    sweep = [(rpm, stats[p][metric][i] if p in stats else math.nan) for rpm, p in points if p in SWEEP_POINTS]
                    refs = [(rpm, stats[p][metric][i]) for rpm, p in points if p in REFERENCE_POINTS and p in stats]
                    if not refs and not any(math.isfinite(y) for _, y in sweep):
                        continue
                    line, = panel.plot([x for x, _ in sweep], [y for _, y in sweep], marker=('o', 's', '^')[i],
                                       color=f'C{index % 10}' if waveform else None, label=label)
                    panel.scatter([x for x, _ in refs], [y for _, y in refs], marker='x', color=line.get_color(), gid=label)
            if panel.lines:
                panel.legend(ncols=len(axes), prop={'family': legend_font} if legend_font else None)
            else:
                message = 'No baseline samples (point 0)' if missing_baseline else 'No samples'
                panel.text(.5, .5, message, transform=panel.transAxes, ha='center')
            if metric == 'ac_rms':
                panel.set_ylim(bottom=0)
    axis_title = '/'.join(axes).upper()
    fig.suptitle(f'{MOTORS[motor]} | IMU {axis_title} axis statistics — {title}')
    save_figure(fig, path, figures)
