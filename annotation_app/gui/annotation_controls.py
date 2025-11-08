"""Control panel for annotation type selection and actions."""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .custom_widgets import MaterialButton


class AnnotationControlPanel(QWidget):
    """Panel with controls for selecting annotation types and performing annotation actions."""

    annotation_changed = pyqtSignal(str)
    fit_annotation_requested = pyqtSignal()
    clear_pupil_requested = pyqtSignal()
    clear_iris_requested = pyqtSignal()
    clear_eyelid_points_requested = pyqtSignal()
    clear_glint_points_requested = pyqtSignal()
    clear_all_requested = pyqtSignal()
    ai_assist_requested = pyqtSignal()
    clear_selected_annotation_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the AnnotationControlPanel."""
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self) -> None:
        """Set up the user interface components."""
        layout = QVBoxLayout()

        # Radio buttons for selecting annotation type
        self.pupil_radio = QRadioButton("Pupil")
        self.iris_radio = QRadioButton("iris")
        self.eyelid_contour_radio = QRadioButton("Eyelid Contour")
        self.glint_radio = QRadioButton("Glint")
        self.pupil_radio.setChecked(True)

        self.button_group = QButtonGroup()
        self.button_group.addButton(self.pupil_radio)
        self.button_group.addButton(self.iris_radio)
        self.button_group.addButton(self.eyelid_contour_radio)
        self.button_group.addButton(self.glint_radio)
        self.button_group.buttonClicked.connect(self.on_annotation_changed)

        # Buttons for fit annotation
        self.fit_button = MaterialButton("Fit Selected Annotation")
        self.fit_button.clicked.connect(self.fit_annotation_requested.emit)

        self.clear_selected_button = MaterialButton("Clear Selected Annotation")
        self.clear_selected_button.clicked.connect(self.clear_selected_annotation_requested.emit)

        self.clear_pupil_button = MaterialButton("Clear Pupil Points")
        self.clear_pupil_button.clicked.connect(self.clear_pupil_requested.emit)

        self.clear_iris_button = MaterialButton("Clear iris Points")
        self.clear_iris_button.clicked.connect(self.clear_iris_requested.emit)

        self.clear_eyelid_points_button = MaterialButton("Clear Eyelid Points")
        self.clear_eyelid_points_button.clicked.connect(self.clear_eyelid_points_requested.emit)

        self.clear_glint_points_button = MaterialButton("Clear Glint Points")
        self.clear_glint_points_button.clicked.connect(self.clear_glint_points_requested.emit)

        self.clear_all_button = MaterialButton("Clear All")
        self.clear_all_button.clicked.connect(self.clear_all_requested.emit)

        self.ai_assist_button = MaterialButton("AI Assist")
        self.ai_assist_button.clicked.connect(self.ai_assist_requested.emit)

        # Add widgets to layout
        layout.addWidget(self.pupil_radio)
        layout.addWidget(self.iris_radio)
        layout.addWidget(self.eyelid_contour_radio)
        layout.addWidget(self.glint_radio)
        layout.addWidget(self.fit_button)
        layout.addWidget(self.clear_selected_button)
        layout.addWidget(self.clear_pupil_button)
        layout.addWidget(self.clear_iris_button)
        layout.addWidget(self.clear_eyelid_points_button)
        layout.addWidget(self.clear_glint_points_button)
        layout.addWidget(self.clear_all_button)
        layout.addWidget(self.ai_assist_button)
        layout.addStretch(1)

        self.setLayout(layout)

    def on_annotation_changed(self, button: QAbstractButton) -> None:
        """Handle annotation type selection change."""
        if button == self.pupil_radio:
            annotation_type = "pupil"
        elif button == self.iris_radio:
            annotation_type = "iris"
        elif button == self.eyelid_contour_radio:
            annotation_type = "eyelid_contour"
        else:  # glint_radio
            annotation_type = "glint"
        self.annotation_changed.emit(annotation_type)

    def set_current_annotation(self, annotation_type: str) -> None:
        """Set the current annotation type."""
        if annotation_type == "pupil":
            self.pupil_radio.setChecked(True)
        elif annotation_type == "iris":
            self.iris_radio.setChecked(True)
        elif annotation_type == "eyelid_contour":
            self.eyelid_contour_radio.setChecked(True)
        else:  # glint
            self.glint_radio.setChecked(True)

    def get_current_annotation_type(self) -> str:
        """Get the currently selected annotation type."""
        if self.pupil_radio.isChecked():
            return "pupil"
        if self.iris_radio.isChecked():
            return "iris"
        if self.eyelid_contour_radio.isChecked():
            return "eyelid_contour"
        return "glint"
