"""Typography and flat, palette-aware styling for the IMU workbench."""
from PySide6.QtGui import QFontInfo, QPalette


def apply_theme(app):
    font = app.font()
    font.setPointSizeF(max(13.0, QFontInfo(font).pointSizeF() + 2.0))
    font.setFamilies([*font.families(), 'Microsoft YaHei UI', 'PingFang SC', 'Noto Sans CJK SC'])
    app.setFont(font)

    dark = app.palette().color(QPalette.ColorRole.Window).lightness() < 128
    paper, field, line, ink, secondary = (
        ('#202426', '#292e30', '#444c4e', '#eef1ef', '#b2bdba') if dark else
        ('#ffffff', '#f6f7f7', '#d8dedd', '#202827', '#566360')
    )
    accent, hover = ('#88cebc', '#30413e') if dark else ('#236656', '#edf4f1')
    caution, caution_bg = ('#edd2a6', '#38332b') if dark else ('#755629', '#faf7f0')
    app.setStyleSheet(f'''
        QWidget#workbench, QTabWidget > QStackedWidget > QWidget {{ background: {paper}; }}
        QLabel, QCheckBox {{ color: {ink}; }}
        QLabel#sectionTitle {{ font-weight: bold; }}
        QLabel#sectionNote, QLabel#connectionState {{ color: {secondary}; }}
        QLabel#benchWarning {{ color: {caution}; background: {caution_bg};
            border-left: 2px solid #ae8a51; padding: 6px 10px; }}
        QTabWidget::pane {{ border: 0; border-top: 1px solid {line}; background: {paper}; }}
        QTabBar::tab {{ background: {paper}; color: {secondary}; padding: 7px 14px;
            border: 0; border-bottom: 2px solid transparent; }}
        QTabBar::tab:hover {{ background: {field}; }}
        QTabBar::tab:selected {{ color: {accent}; border-bottom-color: {accent}; font-weight: bold; }}
        QLineEdit, QComboBox {{ background: {field}; color: {ink};
            border: 1px solid {line}; border-radius: 4px; padding: 3px 6px; }}
        QComboBox QLineEdit, QAbstractSpinBox QLineEdit {{ background: transparent; border: 0; padding: 0; }}
        QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{ color: palette(mid); }}
        QPushButton {{ background: {paper}; color: {ink}; border: 1px solid {line};
            border-radius: 4px; padding: 4px 12px; }}
        QPushButton:hover:enabled {{ background: {hover}; }}
        QPushButton:pressed:enabled {{ background: {field}; border-color: {accent}; }}
        QPushButton:disabled {{ background: {field}; color: {secondary}; border-color: {field}; }}
        QPushButton#connectButton:enabled {{ background: #245f52; color: white; border-color: #245f52; }}
        QPushButton#connectButton:hover:enabled {{ background: #317361; }}
        QPushButton#connectButton:pressed:enabled {{ background: #174a3e; }}
        QPushButton#connectButton:focus {{ border-color: palette(highlight); }}
        QPushButton:focus, QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {{ border-color: {accent}; }}
        QPlainTextEdit, QListWidget, QTableWidget {{ background: {field}; color: {ink};
            border: 1px solid {line}; border-radius: 4px; padding: 6px; }}
    ''')
