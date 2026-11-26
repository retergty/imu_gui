"""Interactive Qt view shared by report plots and on-demand spectra."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QSizePolicy, QVBoxLayout, QWidget

# isort: split
# Import PySide6 first so QtAgg uses the same binding as the workbench.
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT


def toggle_legend(figure, x, y):
    """Toggle a legend entry hit in Matplotlib renderer coordinates."""
    renderer = figure.canvas.get_renderer()
    for ax in figure.axes:
        legend = ax.get_legend()
        if legend is None:
            continue
        for text, handle in zip(legend.get_texts(), legend.legend_handles):
            box = text.get_window_extent(renderer)
            # Include the swatch and the gap before its label (also for scatter handles).
            swatch = renderer.points_to_pixels(text.get_fontsize()) * (legend.handlelength + legend.handletextpad)
            if box.x0 - swatch <= x <= box.x1 and box.y0 <= y <= box.y1:
                label = text.get_text()
                artists = [a for a in (*ax.lines, *ax.collections)
                           if a.get_label() == label or a.get_gid() == label]
                if artists:
                    visible = not artists[0].get_visible()
                    for artist in artists:
                        artist.set_visible(visible)
                    for item in (handle, text):
                        item.set_alpha(1 if visible else .35)
                    return True
                return False
    return False


class PlotToolbar(NavigationToolbar2QT):
    toolitems = (
        ('复位', '恢复初始坐标范围', 'home', 'home'),
        ('后退', '返回上一视图', 'back', 'back'),
        ('前进', '前往下一视图', 'forward', 'forward'),
        (None, None, None, None),
        ('平移', '左键拖动平移；再次点击退出', 'move', 'pan'),
        ('框选', '左键框选放大，右键框选缩小；再次点击退出', 'zoom_to_rect', 'zoom'),
        ('另存图像', '将当前图形视图另存为图片', 'filesave', 'save_figure'),
    )


class ScrollingCanvas(FigureCanvasQTAgg):
    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)
        else:
            event.ignore()  # Let the enclosing scroll area handle native wheel/trackpad input.


class CurvePreview(QWidget):
    def __init__(self, figure, *, min_plot_height=0):
        super().__init__()
        self.figure = figure
        self.canvas = ScrollingCanvas(figure) if min_plot_height else FigureCanvasQTAgg(figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.canvas.setToolTip('滚轮以鼠标为中心缩放；点击图例可隐藏或恢复曲线。')
        self.toolbar = PlotToolbar(self.canvas, self)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        if min_plot_height:
            self.scroll_area = QScrollArea()
            self.scroll_area.setWidgetResizable(True)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.canvas.setMinimumHeight(min_plot_height)
            self.canvas.setToolTip('滚轮上下浏览；Ctrl + 滚轮缩放；点击图例可隐藏或恢复曲线。')
            self.scroll_area.setWidget(self.canvas)
            layout.addWidget(self.scroll_area, 1)
        else:
            layout.addWidget(self.canvas, 1)
        self.toolbar.push_current()  # Home and Back also work when the first action is a wheel zoom.
        self.canvas.mpl_connect('button_press_event', self.click_legend)
        self.canvas.mpl_connect('scroll_event', self.scroll_zoom)

    def click_legend(self, event):
        if event.button == 1 and not self.toolbar.mode and toggle_legend(self.figure, event.x, event.y):
            self.canvas.draw_idle()

    def scroll_zoom(self, event):
        ax = event.inaxes
        if (ax is None or not ax.get_navigate() or event.xdata is None or event.ydata is None
                or not event.step or event.buttons):
            return
        # All workbench axes are linear. Updating only the hit axes lets Matplotlib
        # propagate shared limits (notably FFT/Welch frequency) exactly once.
        scale = 1.25 ** -max(-10, min(10, event.step))
        x, y = event.xdata, event.ydata
        ax.set_xlim(*(x + (v - x) * scale for v in ax.get_xlim()))
        ax.set_ylim(*(y + (v - y) * scale for v in ax.get_ylim()))
        self.toolbar.push_current()
        self.canvas.draw_idle()
