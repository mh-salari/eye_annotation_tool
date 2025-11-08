"""Handler for application menu bar setup and actions."""

from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QAction, QMenu

if TYPE_CHECKING:
    from .main_window import MainWindow


class MenuHandler:
    """Manages the application menu bar and menu actions."""

    def __init__(self, main_window: "MainWindow") -> None:
        """Initialize the MenuHandler."""
        self.main_window = main_window

    def setup_menu(self) -> None:
        """Set up the application menu bar."""
        menubar = self.main_window.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")
        self.add_file_menu_actions(file_menu)

        # AI Configuration menu
        ai_menu = menubar.addMenu("AI Configuration")
        self.add_ai_menu_actions(ai_menu)

        # Help menu
        help_menu = menubar.addMenu("Help")
        self.add_help_menu_actions(help_menu)

    def add_file_menu_actions(self, file_menu: QMenu) -> None:
        """Add actions to the File menu."""
        load_action = QAction("Load Images", self.main_window)
        load_action.triggered.connect(self.main_window.load_images)
        file_menu.addAction(load_action)

        save_action = QAction("Save Annotations", self.main_window)
        save_action.triggered.connect(self.main_window.annotation_controller.save_annotations)
        file_menu.addAction(save_action)

        exit_action = QAction("Exit", self.main_window)
        exit_action.triggered.connect(self.main_window.close)
        file_menu.addAction(exit_action)

    def add_ai_menu_actions(self, ai_menu: QMenu) -> None:
        """Add actions to the AI Configuration menu."""
        pupil_menu = QMenu("Pupil Detector", self.main_window)
        ai_menu.addMenu(pupil_menu)
        self.add_detector_actions(pupil_menu, "pupil_detector")

        iris_menu = QMenu("iris Detector", self.main_window)
        ai_menu.addMenu(iris_menu)
        self.add_detector_actions(iris_menu, "iris_detector")

        eyelid_menu = QMenu("Eyelid Detector", self.main_window)
        ai_menu.addMenu(eyelid_menu)
        self.add_detector_actions(eyelid_menu, "eyelid_detector")

    def add_detector_actions(self, menu: QMenu, detector_type: str) -> None:
        """Add detector selection actions to a menu."""
        if detector_type == "pupil_detector":
            detectors = self.main_window.plugin_manager.get_pupil_detector_names()
        elif detector_type == "iris_detector":
            detectors = self.main_window.plugin_manager.get_iris_detector_names()
        else:  # eyelid_detector
            detectors = self.main_window.plugin_manager.get_eyelid_detector_names()

        for detector in detectors:
            action = QAction(detector, self.main_window)
            action.setCheckable(True)
            action.setChecked(self.main_window.settings_handler.get_setting(detector_type) == detector)
            action.triggered.connect(
                lambda _checked=None, d=detector: self.main_window.change_detector(detector_type, d)
            )
            menu.addAction(action)

        disable_action = QAction("Disable", self.main_window)
        disable_action.setCheckable(True)
        disable_action.setChecked(self.main_window.settings_handler.get_setting(detector_type) == "disabled")
        disable_action.triggered.connect(lambda: self.main_window.change_detector(detector_type, "disabled"))
        menu.addAction(disable_action)

    def update_menu_checks(self) -> None:
        """Update menu item checked states."""
        ai_menu = self.main_window.menuBar().actions()[1].menu()
        pupil_menu = ai_menu.actions()[0].menu()
        iris_menu = ai_menu.actions()[1].menu()
        eyelid_menu = ai_menu.actions()[2].menu()

        self.update_detector_menu_checks(pupil_menu, "pupil_detector")
        self.update_detector_menu_checks(iris_menu, "iris_detector")
        self.update_detector_menu_checks(eyelid_menu, "eyelid_detector")

    def update_detector_menu_checks(self, menu: QMenu, detector_type: str) -> None:
        """Update detector menu item checked states."""
        current_detector = self.main_window.settings_handler.get_setting(detector_type)
        for action in menu.actions():
            if action.text().startswith("Disable"):
                action.setChecked(current_detector == "disabled")
            else:
                action.setChecked(action.text() == current_detector)

    def add_help_menu_actions(self, help_menu: QMenu) -> None:
        """Add actions to the Help menu."""
        about_action = QAction("About", self.main_window)
        about_action.triggered.connect(self.main_window.show_about_dialog)
        help_menu.addAction(about_action)
