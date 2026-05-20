"""Application menu bar setup: File + Help.

Detector picking lives in the side-panel cards (Off / Manual / cheshm
detector id…) — the menu only carries project lifecycle and help.
"""

from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QAction, QMenu

if TYPE_CHECKING:
    from .main_window import MainWindow


class MenuHandler:
    """Build the File and Help menus on the main window's menu bar."""

    def __init__(self, main_window: "MainWindow") -> None:
        self.main_window = main_window

    def setup_menu(self) -> None:
        menubar = self.main_window.menuBar()
        self._add_file_menu(menubar.addMenu("File"))
        self._add_help_menu(menubar.addMenu("Help"))

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

        settings_action = QAction("Project Settings…", self.main_window)
        settings_action.triggered.connect(self.main_window.on_project_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self.main_window)
        exit_action.triggered.connect(self.main_window.close)
        file_menu.addAction(exit_action)

    def _add_help_menu(self, help_menu: QMenu) -> None:
        about_action = QAction("About", self.main_window)
        about_action.triggered.connect(self.main_window.show_about_dialog)
        help_menu.addAction(about_action)
