import os
import ast

from PyQt5.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QFileDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QApplication,
)
from PyQt5.QtGui import QIcon, QPixmap, QCloseEvent
from PyQt5.QtCore import Qt, QEvent, QRect

from .image_viewer import ImageViewer
from .annotation_controls import AnnotationControlPanel
from .custom_widgets import MaterialButton
from .menu_handler import MenuHandler
from .shortcut_handler import ShortcutHandler
from .ai_assist_handler import AIAssistHandler
from ..controllers.navigation_controller import NavigationController
from ..controllers.annotation_controller import AnnotationController
from ..utils.settings_handler import SettingsHandler
from ai import PluginManager


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EyE Annotation Tool")
        self.settings_handler = SettingsHandler()
        self.plugin_manager = PluginManager()

        self.setup_ui()
        self.setup_variables()

        self.annotation_controller = AnnotationController(self)
        self.navigation_controller = NavigationController(self)
        self.menu_handler = MenuHandler(self)
        self.shortcut_handler = ShortcutHandler(self)
        self.ai_assist_handler = AIAssistHandler(self)

        self.menu_handler.setup_menu()
        self.shortcut_handler.setup_shortcuts()
        self.connect_signals()

        # Set the application icon
        icon_path = os.path.join(
            os.path.dirname(__file__), "..", "resources", "app_icon.ico"
        )
        self.setWindowIcon(QIcon(icon_path))

        # Store the screen size for later use
        self.screen = QApplication.primaryScreen().availableGeometry()

        # Set the window to maximized state
        self.showMaximized()

        # Install event filter to catch window state changes
        self.installEventFilter(self)

    def setup_ui(self):
        central_widget = QWidget()
        main_layout = QHBoxLayout()

        # Left panel
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        self.load_images_button = MaterialButton("Load Images")
        self.prev_image_button = MaterialButton("Previous Image")
        self.next_image_button = MaterialButton("Next Image")
        self.save_annotations_button = MaterialButton("Save Annotations")

        left_layout.addWidget(self.load_images_button)
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

    def setup_variables(self):
        self.image_paths = []
        self.current_image_index = -1
        self.annotation_modified = False

    def set_annotation_modified(self, modified):
        self.annotation_modified = modified

    def on_annotation_changed(self):
        self.set_annotation_modified(True)

    def connect_signals(self):
        self.load_images_button.clicked.connect(self.load_images)
        self.prev_image_button.clicked.connect(self.navigation_controller.prev_image)
        self.next_image_button.clicked.connect(self.navigation_controller.next_image)
        self.save_annotations_button.clicked.connect(
            self.annotation_controller.save_annotations
        )
        self.image_list_widget.itemClicked.connect(
            self.navigation_controller.on_image_selected
        )

        self.annotation_controls.annotation_changed.connect(
            self.image_viewer.set_current_annotation
        )
        self.annotation_controls.fit_annotation_requested.connect(
            self.image_viewer.fit_annotation
        )
        self.annotation_controls.clear_selected_annotation_requested.connect(
            self.image_viewer.clear_selected_ellipse
        )
        self.annotation_controls.clear_pupil_requested.connect(
            self.image_viewer.clear_pupil_points
        )
        self.annotation_controls.clear_iris_requested.connect(
            self.image_viewer.clear_iris_points
        )
        self.annotation_controls.clear_eyelid_points_requested.connect(
            self.image_viewer.clear_eyelid_points
        )
        self.annotation_controls.clear_all_requested.connect(
            self.image_viewer.clear_all
        )
        self.annotation_controls.ai_assist_requested.connect(
            self.ai_assist_handler.on_ai_assist_requested
        )

        self.image_viewer.annotation_changed.connect(self.on_annotation_changed)
        self.image_viewer.annotation_type_changed.connect(
            self.annotation_controls.set_current_annotation
        )

    def load_images(self):
        file_dialog = QFileDialog()
        image_files, _ = file_dialog.getOpenFileNames(
            self, "Select Image Files", "", "Image Files (*.png *.jpg *.bmp)"
        )
        if image_files:
            self.image_paths = image_files
            self.current_image_index = 0
            self.update_image_list()
            self.load_current_image()

    def update_image_list(self):
        self.image_list_widget.clear()
        for image_path in self.image_paths:
            self.image_list_widget.addItem(os.path.basename(image_path))
        if self.current_image_index >= 0:
            self.image_list_widget.setCurrentRow(self.current_image_index)

    def load_current_image(self):
        if 0 <= self.current_image_index < len(self.image_paths):
            image_path = self.image_paths[self.current_image_index]
            if self.image_viewer.load_image(image_path):
                self.setWindowTitle(
                    f"EyE Annotation Tool - {os.path.basename(image_path)}"
                )
                self.annotation_controller.load_annotations()
            else:
                QMessageBox.critical(
                    self, "Error", f"Failed to load image: {image_path}"
                )

    def save_current_annotations(self):
        self.annotation_controller.save_current_annotations()

    def on_annotation_changed(self):
        self.set_annotation_modified(True)

    def change_detector(self, detector_type, detector_name):
        self.settings_handler.set_setting(detector_type, detector_name)
        self.menu_handler.update_menu_checks()

    def get_current_screen(self):
        # Get the screen that contains the center of the window
        center = self.geometry().center()
        return QApplication.screenAt(center)

    def resize_to_percentage(self, percentage):
        # Get the current screen
        current_screen = self.get_current_screen()

        if current_screen:
            # Get the available geometry of the current screen
            available_geometry = current_screen.availableGeometry()

            # Calculate the new size
            new_width = int(available_geometry.width() * percentage)
            new_height = int(available_geometry.height() * percentage)

            # Calculate new position to keep the window centered on the current screen
            new_x = (
                available_geometry.x() + (available_geometry.width() - new_width) // 2
            )
            new_y = (
                available_geometry.y() + (available_geometry.height() - new_height) // 2
            )

            # Set the new geometry (position and size)
            new_geometry = QRect(new_x, new_y, new_width, new_height)
            self.setGeometry(new_geometry)

    def center_window(self):
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

    def moveEvent(self, event):
        # The current screen is updated automatically when the window moves
        super().moveEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMaximized:
                # When maximized, keep it maximized (window controls visible)
                pass
            elif self.windowState() == Qt.WindowNoState:
                # When restored, set to 75% of current screen size
                self.resize_to_percentage(0.75)
        return super().eventFilter(obj, event)

    def closeEvent(self, event: QCloseEvent):
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

    def get_version_from_setup(self):
        setup_path = os.path.join(os.path.dirname(__file__), "..", "..", "setup.py")
        with open(setup_path, "r") as file:
            tree = ast.parse(file.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and node.func.id == "setup":
                    for keyword in node.keywords:
                        if keyword.arg == "version":
                            return ast.literal_eval(keyword.value)
        return "Unknown"

    def show_about_dialog(self):
        about_text = (
            "<h3>EyE Annotation Tool</h3>"
            "<p>A tool to annotate eye images for pupil, iris and eyelid detection.</p>"
            "<p>Developed by "
            "<a href='https://mh-salari.ir/'"
            "style='color: #8b7aa2;'>Mohammadhossein Salari</a></p>"
            f"<p>Current version: { self.get_version_from_setup()}</p>"
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
        image_path = os.path.join(
            os.path.dirname(__file__), "..", "resources", "Funded_by_EU_Eyes4ICU.png"
        )
        pixmap = QPixmap(image_path)
        image_label.setPixmap(
            pixmap.scaled(400, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        image_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(image_label)

        about_widget.setLayout(layout)

        # Create and show the message box without an icon
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("About EyE Annotation Tool")
        msg_box.setIcon(QMessageBox.NoIcon)  # This removes the icon
        msg_box.layout().addWidget(
            about_widget, 0, 0, 1, msg_box.layout().columnCount()
        )
        msg_box.exec_()
