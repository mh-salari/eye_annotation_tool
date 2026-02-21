"""Control panel for annotation type selection and actions."""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .custom_widgets import AnnotationGroup, EyeSelector, MaterialButton


class AnnotationControlPanel(QWidget):
    """Panel with controls for selecting annotation types and performing annotation actions."""

    annotation_changed = pyqtSignal(str)
    eye_changed = pyqtSignal(str)
    fit_annotation_requested = pyqtSignal()
    clear_pupil_requested = pyqtSignal()
    clear_iris_requested = pyqtSignal()
    clear_eyelid_points_requested = pyqtSignal()
    clear_glint_points_requested = pyqtSignal()
    clear_all_requested = pyqtSignal()
    ai_assist_requested = pyqtSignal()
    clear_selected_annotation_requested = pyqtSignal()
    roi_toggle_requested = pyqtSignal()
    roi_clear_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the AnnotationControlPanel."""
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self) -> None:
        """Set up the user interface components."""
        layout = QVBoxLayout()

        self.eye_selector = EyeSelector()
        self.eye_selector.eye_changed.connect(self.eye_changed.emit)
        layout.addWidget(self.eye_selector)

        # ROI buttons
        roi_layout = QHBoxLayout()
        self.roi_toggle_button = MaterialButton("ROI Mode")
        self.roi_toggle_button.setCheckable(True)
        self.roi_toggle_button.clicked.connect(self.roi_toggle_requested.emit)
        roi_layout.addWidget(self.roi_toggle_button)

        self.roi_clear_button = MaterialButton("Clear ROI")
        self.roi_clear_button.clicked.connect(self.roi_clear_requested.emit)
        roi_layout.addWidget(self.roi_clear_button)
        layout.addLayout(roi_layout)

        title_label = QLabel("Annotation Types")
        title_label.setStyleSheet(
            """
            QLabel {
                font-size: 16px;
                font-weight: bold;
                color: #00bcd4;
                padding: 10px 0;
            }
        """
        )
        layout.addWidget(title_label)

        self.pupil_group = AnnotationGroup("Pupil", has_fit=True, has_ai_assist=True)
        self.pupil_group.selected.connect(lambda: self.annotation_changed.emit("pupil"))
        self.pupil_group.fit_requested.connect(self.on_fit_requested)
        self.pupil_group.clear_requested.connect(self.clear_pupil_requested.emit)
        self.pupil_group.ai_assist_requested.connect(self.ai_assist_requested.emit)
        self.pupil_group.set_checked(True)

        self.iris_group = AnnotationGroup("Iris", has_fit=True, has_ai_assist=True)
        self.iris_group.selected.connect(lambda: self.annotation_changed.emit("iris"))
        self.iris_group.fit_requested.connect(self.on_fit_requested)
        self.iris_group.clear_requested.connect(self.clear_iris_requested.emit)
        self.iris_group.ai_assist_requested.connect(self.ai_assist_requested.emit)

        self.eyelid_group = AnnotationGroup("Eyelid Contour", has_fit=False, has_ai_assist=True)
        self.eyelid_group.selected.connect(lambda: self.annotation_changed.emit("eyelid_contour"))
        self.eyelid_group.clear_requested.connect(self.clear_eyelid_points_requested.emit)
        self.eyelid_group.ai_assist_requested.connect(self.ai_assist_requested.emit)

        self.glint_group = AnnotationGroup("Glint", has_fit=False, has_ai_assist=True)
        self.glint_group.selected.connect(lambda: self.annotation_changed.emit("glint"))
        self.glint_group.clear_requested.connect(self.clear_glint_points_requested.emit)
        self.glint_group.ai_assist_requested.connect(self.ai_assist_requested.emit)

        self.button_group = QButtonGroup()
        self.button_group.addButton(self.pupil_group.radio)
        self.button_group.addButton(self.iris_group.radio)
        self.button_group.addButton(self.eyelid_group.radio)
        self.button_group.addButton(self.glint_group.radio)

        layout.addWidget(self.pupil_group)
        layout.addWidget(self.iris_group)
        layout.addWidget(self.eyelid_group)
        layout.addWidget(self.glint_group)

        layout.addStretch(1)

        self.clear_all_button = MaterialButton("Clear All")
        self.clear_all_button.clicked.connect(self.clear_all_requested.emit)
        layout.addWidget(self.clear_all_button)

        self.setLayout(layout)

    def on_fit_requested(self) -> None:
        """Handle fit annotation request."""
        self.fit_annotation_requested.emit()

    def set_current_annotation(self, annotation_type: str) -> None:
        """Set the current annotation type."""
        if annotation_type == "pupil":
            self.pupil_group.set_checked(True)
        elif annotation_type == "iris":
            self.iris_group.set_checked(True)
        elif annotation_type == "eyelid_contour":
            self.eyelid_group.set_checked(True)
        else:
            self.glint_group.set_checked(True)

    def get_current_annotation_type(self) -> str:
        """Get the currently selected annotation type."""
        if self.pupil_group.is_checked():
            return "pupil"
        if self.iris_group.is_checked():
            return "iris"
        if self.eyelid_group.is_checked():
            return "eyelid_contour"
        return "glint"

    def get_current_eye(self) -> str:
        """Get the currently selected eye."""
        return self.eye_selector.get_current_eye()

    def set_current_eye(self, eye: str) -> None:
        """Set the currently selected eye."""
        self.eye_selector.set_current_eye(eye)
