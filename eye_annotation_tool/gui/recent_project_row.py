"""A single recent-project row: open button plus a remove (x) button."""

import qtawesome as qta
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QPushButton, QToolButton, QWidget

from .theme import theme


class RecentProjectRow(QWidget):
    """One recent-project row: an open button plus a remove (x) button."""

    open_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, project_path: str, label: str, exists: bool, parent: QWidget | None = None) -> None:
        """Build the row; greyed out and non-openable when ``exists`` is False."""
        super().__init__(parent)
        self._path = project_path
        tooltip = project_path if exists else f"{project_path}\n(project file not found)"
        self.setToolTip(tooltip)
        # Highlight the whole row on hover so the delete target is unambiguous.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"RecentProjectRow:hover {{ background-color: {theme.color('hover_bg')}; border-radius: 4px; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        open_button = QPushButton(label)
        open_button.setStyleSheet(
            "QPushButton { background: transparent; border: none; text-align: left; padding: 4px 6px; }"
            f"QPushButton:disabled {{ color: {theme.color('muted_fg')}; }}"
        )
        open_button.setToolTip(tooltip)
        open_button.setEnabled(exists)
        open_button.clicked.connect(lambda: self.open_requested.emit(self._path))
        remove_button = QToolButton()
        remove_button.setIcon(qta.icon("mdi6.close", color=theme.color("icon_subtle")))
        remove_button.setToolTip("Remove from the recent list (the project file is kept)")
        remove_button.setStyleSheet(
            "QToolButton { border: none; border-radius: 4px; }"
            f"QToolButton:hover {{ background-color: {theme.color('danger_bg')}; }}"
        )
        remove_button.clicked.connect(lambda: self.remove_requested.emit(self._path))
        row.addWidget(open_button, 1)
        row.addWidget(remove_button)
