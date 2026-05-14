"""Main application window for the eye annotation tool."""

import ast
from pathlib import Path

from PyQt5.QtCore import QEvent, QRect, Qt
from PyQt5.QtGui import QCloseEvent, QIcon, QPixmap, QScreen
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..auto_detectors import PluginManager
from ..controllers.annotation_controller import AnnotationController
from ..controllers.navigation_controller import NavigationController
from ..utils.project_settings import PROJECT_DETECTOR_KEYS, load_project_settings, save_project_settings
from ..utils.settings_handler import SettingsHandler
from .annotation_controls import AnnotationControlPanel
from .auto_detectors_handler import AutoDetectorsHandler
from .custom_widgets import MaterialButton
from .image_viewer import ImageViewer
from .menu_handler import MenuHandler
from .preferences_dialog import PreferencesDialog
from .shortcut_handler import ShortcutHandler


class MainWindow(QMainWindow):
    """Main application window containing all UI components and controllers."""

    def __init__(self, cli_single_eye: bool = False) -> None:
        """Initialize the MainWindow.

        Args:
            cli_single_eye: When True, force single-eye mode on for this
                session regardless of any per-project setting. Equivalent to
                checking the Preferences dialog checkbox at startup.

        """
        super().__init__()
        self.setWindowTitle("EyE Annotation Tool")
        self.settings_handler = SettingsHandler()
        self.plugin_manager = PluginManager()
        self._cli_single_eye = bool(cli_single_eye)
        self.single_eye_mode = self._cli_single_eye
        self.project_dir: str | None = None

        self.setup_ui()
        self.setup_variables()

        self.annotation_controller = AnnotationController(self)
        self.navigation_controller = NavigationController(self)
        self.menu_handler = MenuHandler(self)
        self.shortcut_handler = ShortcutHandler(self)
        self.auto_detectors_handler = AutoDetectorsHandler(self)

        self.menu_handler.setup_menu()
        self.shortcut_handler.setup_shortcuts()
        self.connect_signals()

        # Set the application icon
        icon_path = str(Path(__file__).parent / ".." / "resources" / "app_icon.ico")
        self.setWindowIcon(QIcon(icon_path))

        # Store the screen size for later use
        self.screen = QApplication.primaryScreen().availableGeometry()

        # Set the window to maximized state
        self.showMaximized()

        # Install event filter to catch window state changes
        self.installEventFilter(self)

    def setup_ui(self) -> None:
        """Set up the user interface components."""
        central_widget = QWidget()
        main_layout = QHBoxLayout()

        # Left panel
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        self.load_images_button = MaterialButton("Load Images")
        self.load_folder_button = MaterialButton("Load Folder")
        self.prev_image_button = MaterialButton("Previous Image")
        self.next_image_button = MaterialButton("Next Image")
        self.save_annotations_button = MaterialButton("Save Annotations")

        left_layout.addWidget(self.load_images_button)
        left_layout.addWidget(self.load_folder_button)
        left_layout.addWidget(self.prev_image_button)
        left_layout.addWidget(self.next_image_button)
        left_layout.addWidget(self.save_annotations_button)

        self.image_list_widget = QListWidget()
        left_layout.addWidget(QLabel("Loaded Images:"))
        left_layout.addWidget(self.image_list_widget)

        left_layout.addStretch(1)
        left_panel.setLayout(left_layout)

        # Central area for image viewer
        self.image_viewer = ImageViewer()

        # Right panel for annotation controls
        self.annotation_controls = AnnotationControlPanel()

        main_layout.addWidget(left_panel)
        main_layout.addWidget(self.image_viewer, 1)
        main_layout.addWidget(self.annotation_controls)

        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        # Set focus to the image viewer
        self.image_viewer.setFocus()

    def setup_variables(self) -> None:
        """Initialize instance variables."""
        self.image_paths = []
        self.current_image_index = -1
        self.annotation_modified = False

    def set_annotation_modified(self, modified: bool) -> None:
        """Set the annotation modified flag."""
        self.annotation_modified = modified

    def connect_signals(self) -> None:
        """Connect signals and slots for UI components."""
        self.load_images_button.clicked.connect(self.load_images)
        self.load_folder_button.clicked.connect(self.load_folder)
        self.prev_image_button.clicked.connect(self.navigation_controller.prev_image)
        self.next_image_button.clicked.connect(self.navigation_controller.next_image)
        self.save_annotations_button.clicked.connect(self.annotation_controller.save_annotations)
        self.image_list_widget.itemClicked.connect(self.navigation_controller.on_image_selected)

        self.annotation_controls.annotation_changed.connect(self.image_viewer.set_current_annotation)
        self.annotation_controls.eye_changed.connect(self.image_viewer.switch_eye)
        self.annotation_controls.fit_annotation_requested.connect(self.image_viewer.fit_annotation)
        self.annotation_controls.clear_selected_annotation_requested.connect(self.image_viewer.clear_selected_ellipse)
        self.annotation_controls.clear_pupil_requested.connect(self.image_viewer.clear_pupil_points)
        self.annotation_controls.clear_iris_requested.connect(self.image_viewer.clear_iris_points)
        self.annotation_controls.clear_eyelid_points_requested.connect(self.image_viewer.clear_eyelid_points)
        self.annotation_controls.clear_glint_points_requested.connect(self.image_viewer.clear_glint_points)
        self.annotation_controls.clear_all_requested.connect(self.image_viewer.clear_all)
        self.annotation_controls.auto_detector_requested.connect(self.auto_detectors_handler.on_auto_detector_requested)
        self.annotation_controls.roi_toggle_requested.connect(self.image_viewer.toggle_roi_mode)
        self.annotation_controls.roi_clear_requested.connect(self.image_viewer.clear_roi)

        self.image_viewer.annotation_changed.connect(self.on_annotation_changed)
        self.image_viewer.annotation_type_changed.connect(self.annotation_controls.set_current_annotation)

    IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp")

    def load_images(self) -> None:
        """Open file dialog to load image files."""
        file_dialog = QFileDialog()
        image_files, _ = file_dialog.getOpenFileNames(
            self, "Select Image Files", "", "Image Files (*.png *.jpg *.bmp)"
        )
        if image_files:
            self.image_paths = image_files
            self.current_image_index = 0
            self._load_project_settings_from(image_files[0])
            self.update_image_list()
            self.load_current_image()

    def load_folder(self) -> None:
        """Pick a folder and recursively load every supported image inside it.

        The chosen folder is the project root (project settings live here),
        not whichever subdir an image happens to be in. Useful for the
        psa-mechanisms layout where per-eye images are split across phase
        subdirs (``cal_dark/``, ``cal_bright/``, ...).
        """
        folder = QFileDialog.getExistingDirectory(self, "Select Image Folder", "")
        if not folder:
            return
        folder_path = Path(folder)
        image_paths = sorted(
            str(p) for p in folder_path.rglob("*") if p.is_file() and p.suffix.lower() in self.IMAGE_SUFFIXES
        )
        if not image_paths:
            QMessageBox.warning(
                self,
                "No Images Found",
                f"No image files found under {folder_path}.",
            )
            return
        self.image_paths = image_paths
        self.current_image_index = 0
        self._apply_project_settings(str(folder_path))
        self.update_image_list()
        self.load_current_image()

    def _load_project_settings_from(self, image_path: str) -> None:
        """Read the project settings file in the image's folder and apply it."""
        self._apply_project_settings(str(Path(image_path).parent))

    def _apply_project_settings(self, project_dir: str) -> None:
        """Set the active project directory and apply every value in its file.

        Applies both ``single_eye_mode`` (CLI ``--single-eye`` still wins so
        an explicit invocation override is never silently downgraded) and
        the per-project auto-detector choices. Detector overrides are pushed
        into ``settings_handler`` so the rest of the app sees them, and the
        Auto Detectors menu is refreshed.
        """
        self.project_dir = project_dir
        project_settings = load_project_settings(project_dir)
        effective_single_eye = self._cli_single_eye or bool(project_settings.get("single_eye_mode", False))
        self._apply_single_eye_mode(effective_single_eye)
        detectors_changed = False
        for key in PROJECT_DETECTOR_KEYS:
            if key in project_settings:
                self.settings_handler.set_setting(key, project_settings[key])
                detectors_changed = True
        if detectors_changed:
            self.menu_handler.update_menu_checks()

    def _apply_single_eye_mode(self, enabled: bool) -> None:
        """Propagate the single-eye flag to the dependent widgets."""
        self.single_eye_mode = enabled
        self.image_viewer.set_single_eye_mode(enabled)
        self.annotation_controls.set_single_eye_mode(enabled)

    def show_preferences_dialog(self) -> None:
        """Open the per-project Preferences dialog."""
        current = {"single_eye_mode": self.single_eye_mode}
        dialog = PreferencesDialog(current, self)
        dialog.settings_changed.connect(self._on_preferences_changed)
        dialog.exec_()

    def _on_preferences_changed(self, new_settings: dict) -> None:
        """Persist the dialog's settings and apply them to the GUI.

        When no project folder is loaded yet (user opened Preferences before
        loading images), the in-memory mode is still updated so it takes
        effect immediately; the project file is written the next time
        images are loaded.

        Merges into the existing project file so detector overrides written
        by ``change_detector`` (and any future per-project keys) survive a
        Preferences save.
        """
        enabled = bool(new_settings.get("single_eye_mode", False))
        self._apply_single_eye_mode(enabled)
        if self.project_dir is not None:
            merged = load_project_settings(self.project_dir)
            merged["single_eye_mode"] = enabled
            save_project_settings(self.project_dir, merged)

    def update_image_list(self) -> None:
        """Update the image list widget with current image paths.

        When a project folder is set, display each entry as its path relative
        to that root so files with the same basename in different subdirs are
        distinguishable (e.g. ``cal_dark/left_eye_target_0.png`` vs
        ``cal_bright/left_eye_target_0.png``).
        """
        self.image_list_widget.clear()
        project_root = Path(self.project_dir) if self.project_dir else None
        for image_path in self.image_paths:
            p = Path(image_path)
            if project_root is not None:
                try:
                    label = str(p.relative_to(project_root))
                except ValueError:
                    label = p.name
            else:
                label = p.name
            self.image_list_widget.addItem(label)
        if self.current_image_index >= 0:
            self.image_list_widget.setCurrentRow(self.current_image_index)

    def load_current_image(self) -> None:
        """Load and display the current image with its annotations."""
        if 0 <= self.current_image_index < len(self.image_paths):
            image_path = self.image_paths[self.current_image_index]
            if self.image_viewer.load_image(image_path):
                self.setWindowTitle(f"EyE Annotation Tool - {Path(image_path).name}")
                self.annotation_controller.load_annotations()
            else:
                QMessageBox.critical(self, "Error", f"Failed to load image: {image_path}")

    def save_current_annotations(self) -> None:
        """Save annotations for the current image."""
        self.annotation_controller.save_current_annotations()

    def on_annotation_changed(self) -> None:
        """Handle annotation change event."""
        self.set_annotation_modified(True)

    def change_detector(self, detector_type: str, detector_name: str) -> None:
        """Change the active detector for a given type.

        Updates the global settings file (so the choice persists across
        sessions when no project is loaded) and, if a project is loaded,
        mirrors the choice into the per-project settings file so opening
        the same project later restores the same detectors.
        """
        self.settings_handler.set_setting(detector_type, detector_name)
        if self.project_dir is not None and detector_type in PROJECT_DETECTOR_KEYS:
            project_settings = load_project_settings(self.project_dir)
            project_settings[detector_type] = detector_name
            save_project_settings(self.project_dir, project_settings)
        self.menu_handler.update_menu_checks()

    def get_current_screen(self) -> QScreen | None:
        # Get the screen that contains the center of the window
        """Get the screen that currently contains the window."""
        center = self.geometry().center()
        return QApplication.screenAt(center)

    def resize_to_percentage(self, percentage: float) -> None:
        # Get the current screen
        """Resize the window to a percentage of screen size."""
        current_screen = self.get_current_screen()

        if current_screen:
            # Get the available geometry of the current screen
            available_geometry = current_screen.availableGeometry()

            # Calculate the new size
            new_width = int(available_geometry.width() * percentage)
            new_height = int(available_geometry.height() * percentage)

            # Calculate new position to keep the window centered on the current screen
            new_x = available_geometry.x() + (available_geometry.width() - new_width) // 2
            new_y = available_geometry.y() + (available_geometry.height() - new_height) // 2

            # Set the new geometry (position and size)
            new_geometry = QRect(new_x, new_y, new_width, new_height)
            self.setGeometry(new_geometry)

    def center_window(self) -> None:
        """Center the window on the current screen."""
        current_screen = self.get_current_screen()

        if current_screen:
            # Get the geometry of the current screen
            screen_geometry = current_screen.geometry()

            # Calculate the center point of the screen
            center_point = screen_geometry.center()

            # Move the window to the center of the current screen
            frame_geometry = self.frameGeometry()
            frame_geometry.moveCenter(center_point)
            self.move(frame_geometry.topLeft())

    def moveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle window move events."""
        # The current screen is updated automatically when the window moves
        super().moveEvent(event)

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:  # noqa: N802
        """Filter events for window state changes."""
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMaximized:
                # When maximized, keep it maximized (window controls visible)
                pass
            elif self.windowState() == Qt.WindowNoState:
                # When restored, set to 75% of current screen size
                self.resize_to_percentage(0.75)
        return super().eventFilter(obj, event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Handle window close event."""
        if self.annotation_modified:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Do you want to save before exiting?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )

            if reply == QMessageBox.Save:
                self.annotation_controller.save_annotations()
                event.accept()
            elif reply == QMessageBox.Discard:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    @staticmethod
    def get_version_from_setup() -> str:
        """Get the application version from setup.py."""
        setup_path = str(Path(__file__).parent / ".." / ".." / "setup.py")
        with Path(setup_path).open(encoding="utf-8") as file:
            tree = ast.parse(file.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and node.func.id == "setup":
                    for keyword in node.keywords:
                        if keyword.arg == "version":
                            return ast.literal_eval(keyword.value)
        return "Unknown"

    def show_about_dialog(self) -> None:
        """Show the about dialog with application information."""
        about_text = (
            "<h3>EyE Annotation Tool</h3>"
            "<p>A tool to annotate eye images for pupil, iris and eyelid detection.</p>"
            "<p>Developed by "
            "<a href='https://mh-salari.ir/'"
            "style='color: #8b7aa2;'>Mohammadhossein Salari</a></p>"
            f"<p>Current version: {self.get_version_from_setup()}</p>"
            "<p>To get the latest version of Eye Annotation Tool, visit<br>"
            "<a href='https://github.com/mh-salari/eye_annotation_tool' "
            "style='color: #8b7aa2;' target='_blank' rel='noopener noreferrer'>"
            "github.com/mh-salari/eye_annotation_tool</a></p>"
            "<p>This project has received funding from the European Union's Horizon "
            "Europe research and innovation funding program under grant "
            "agreement No 101072410, Eyes4ICU project.</p>"
        )
        # Create a custom widget for the about dialog
        about_widget = QWidget()
        layout = QVBoxLayout()

        # Add text
        text_label = QLabel(about_text)
        text_label.setTextFormat(Qt.RichText)
        text_label.setOpenExternalLinks(True)
        text_label.setWordWrap(True)
        layout.addWidget(text_label)

        # Add image
        image_label = QLabel()
        image_path = str(Path(__file__).parent / ".." / "resources" / "Funded_by_EU_Eyes4ICU.png")
        pixmap = QPixmap(image_path)
        image_label.setPixmap(pixmap.scaled(400, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        image_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(image_label)

        about_widget.setLayout(layout)

        # Create and show the message box without an icon
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("About EyE Annotation Tool")
        msg_box.setIcon(QMessageBox.NoIcon)  # This removes the icon
        msg_box.layout().addWidget(about_widget, 0, 0, 1, msg_box.layout().columnCount())
        msg_box.exec_()
