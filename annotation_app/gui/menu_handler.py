"""Application menu bar setup.

Holds File and Help menus only. Per-plugin defaults UI lives inside each
plugin's panel (see plugin contract); there is no global Auto Detectors
chooser menu.
"""

from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QAction, QMenu

if TYPE_CHECKING:
    from .main_window import MainWindow


class MenuHandler:
    """Build the application's menu bar."""

    def __init__(self, main_window: "MainWindow") -> None:
        """Initialise with a back-reference to the main window."""
        self.main_window = main_window

    def setup_menu(self) -> None:
        """Construct the File and Help menus on the main window's menu bar."""
        menubar = self.main_window.menuBar()
        self._add_file_menu(menubar.addMenu("File"))
        self._add_help_menu(menubar.addMenu("Help"))

    def _add_file_menu(self, file_menu: QMenu) -> None:
        load_action = QAction("Load Images", self.main_window)
        load_action.triggered.connect(self.main_window.load_images)
        file_menu.addAction(load_action)

        save_action = QAction("Save Annotations", self.main_window)
        save_action.triggered.connect(self.main_window.annotation_controller.save_annotations)
        file_menu.addAction(save_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self.main_window)
        exit_action.triggered.connect(self.main_window.close)
        file_menu.addAction(exit_action)

    def _add_help_menu(self, help_menu: QMenu) -> None:
        about_action = QAction("About", self.main_window)
        about_action.triggered.connect(self.main_window.show_about_dialog)
        help_menu.addAction(about_action)
