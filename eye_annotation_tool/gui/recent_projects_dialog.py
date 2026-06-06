"""Dialog to manage the recent-projects list: multi-select, clear selected, clear all."""

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeyEvent
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..state import recent_projects
from ..utils.project_settings import PROJECT_FILE_SUFFIX
from .dialogs import confirm
from .theme import theme

_PATH_ROLE = Qt.UserRole


class _RecentList(QListWidget):
    """Multi-select list of recent projects; Delete removes the selection."""

    remove_requested = pyqtSignal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def selected_paths(self) -> list[str]:
        """Return the paths of the currently-selected rows."""
        return [it.data(_PATH_ROLE) for it in self.selectedItems()]

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Map Delete / Backspace to a removal request for the selected rows."""
        if event.key() in {Qt.Key_Delete, Qt.Key_Backspace}:
            paths = self.selected_paths()
            if paths:
                self.remove_requested.emit(paths)
                return
        super().keyPressEvent(event)


class RecentProjectsDialog(QDialog):
    """Manage the recent-projects list: remove the selection or clear all."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the dialog and populate it from the persisted recent list."""
        super().__init__(parent)
        self.setWindowTitle("Recent Projects")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        self._list = _RecentList(self)
        self._list.remove_requested.connect(self._on_remove)
        self._list.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self._list)

        button_row = QHBoxLayout()
        self._clear_selected_button = QPushButton("Clear Selected")
        self._clear_selected_button.clicked.connect(lambda: self._on_remove(self._list.selected_paths()))
        self._clear_all_button = QPushButton("Clear All")
        self._clear_all_button.clicked.connect(self._on_clear_all)
        for button in (self._clear_selected_button, self._clear_all_button):
            button.setAutoDefault(False)
            button.setDefault(False)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_row.addWidget(self._clear_selected_button)
        button_row.addWidget(self._clear_all_button)
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        self._reload()

    def _reload(self) -> None:
        """Repopulate the list from disk, greying out missing project files."""
        self._list.clear()
        for path in recent_projects.load():
            exists = Path(path).exists()
            item = QListWidgetItem(Path(path).name.removesuffix(PROJECT_FILE_SUFFIX))
            item.setData(_PATH_ROLE, path)
            item.setToolTip(path if exists else f"{path}\n(project file not found)")
            if not exists:
                item.setForeground(QColor(theme.color("muted_fg")))
            self._list.addItem(item)
        self._update_buttons()

    def _update_buttons(self) -> None:
        """Enable Clear Selected only with a selection; Clear All only with rows."""
        self._clear_selected_button.setEnabled(bool(self._list.selectedItems()))
        self._clear_all_button.setEnabled(self._list.count() > 0)

    def _on_remove(self, paths: list[str]) -> None:
        """Remove the given recent entries after confirmation."""
        if not paths:
            return
        count = len(paths)
        message = (
            "Remove this project from the recent list?\nThe project file is not deleted."
            if count == 1
            else f"Remove {count} projects from the recent list?\nThe project files are not deleted."
        )
        if confirm(self, "Remove from recent", message):
            for path in paths:
                recent_projects.remove(path)
            self._reload()

    def _on_clear_all(self) -> None:
        """Empty the entire recent list after confirmation."""
        if not recent_projects.load():
            return
        if confirm(
            self,
            "Clear recent projects",
            "Clear the entire recent-projects list?\nThe project files are not deleted.",
        ):
            recent_projects.clear()
            self._reload()
