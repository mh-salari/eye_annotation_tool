"""Application menu bar setup: File + Help.

Detector picking lives in the side-panel cards (Off / Manual / cheshm
detector id…) — the menu only carries project lifecycle and help.
"""

from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QAction, QActionGroup, QMenu

from ..state import settings
from .theme import theme

if TYPE_CHECKING:
    from .main_window import MainWindow


class MenuHandler:
    """Build the File and Help menus on the main window's menu bar."""

    def __init__(self, main_window: "MainWindow") -> None:
        """Store the main window the menus attach to."""
        self.main_window = main_window

    def setup_menu(self) -> None:
        """Build the File, Settings, and Help menus on the menu bar."""
        menubar = self.main_window.menuBar()
        self._add_file_menu(menubar.addMenu("File"))
        self._add_settings_menu(menubar.addMenu("Settings"))
        self._add_help_menu(menubar.addMenu("Help"))

    def _add_settings_menu(self, menu: QMenu) -> None:
        """Build the Settings menu: an app-wide Theme submenu plus Project Settings."""
        theme_menu = menu.addMenu("Theme")
        group = QActionGroup(self.main_window)
        group.setExclusive(True)
        for label, mode in (("System", "system"), ("Light", "light"), ("Dark", "dark")):
            action = QAction(label, self.main_window)
            action.setCheckable(True)
            action.setChecked(theme.mode == mode)
            action.triggered.connect(lambda *_, m=mode: self._on_theme_selected(m))
            group.addAction(action)
            theme_menu.addAction(action)

        menu.addSeparator()
        project_settings_action = QAction("Project Settings…", self.main_window)
        project_settings_action.triggered.connect(self.main_window.on_project_settings)
        menu.addAction(project_settings_action)

    @staticmethod
    def _on_theme_selected(mode: str) -> None:
        """Apply and persist the chosen theme."""
        theme.apply(mode)
        settings.save_theme(mode)

    def _add_file_menu(self, file_menu: QMenu) -> None:
        new_action = QAction("New Project", self.main_window)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self.main_window.on_new_project)
        file_menu.addAction(new_action)

        open_action = QAction("Open Project…", self.main_window)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.main_window.on_open_project)
        file_menu.addAction(open_action)

        save_action = QAction("Save Project", self.main_window)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.main_window.save_project)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save Project As…", self.main_window)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self.main_window.save_project_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self.main_window)
        exit_action.triggered.connect(self.main_window.close)
        file_menu.addAction(exit_action)

    def _add_help_menu(self, help_menu: QMenu) -> None:
        about_action = QAction("About", self.main_window)
        about_action.triggered.connect(self.main_window.show_about_dialog)
        help_menu.addAction(about_action)
