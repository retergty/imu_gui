"""Render the report image and optionally keep its Agg canvas for GUI interaction."""
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
