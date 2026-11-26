"""Small UI operations shared by workbench pages."""
from pathlib import Path

from PySide6.QtWidgets import QPushButton


def button(layout, title, callback):
    btn = QPushButton(title)
    btn.clicked.connect(callback)
    layout.addWidget(btn)
    return btn


def last_dir(settings, key, fallback=None):
    path = Path(settings.value(key, str(fallback or Path.home()), type=str))
    return path if path.is_dir() else Path.home()


def restore_tab(tabs, name):
    for index in range(tabs.count()):
        if tabs.tabText(index) == name:
            tabs.setCurrentIndex(index)
            break
