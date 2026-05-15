"""Control panel for annotation type selection and actions."""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .custom_widgets import AnnotationGroup, EyeSelector, MaterialButton
from .manual_threshold_panel import ManualThresholdPanel

MODE_ANNOTATE = "annotate"
MODE_MANUAL_THRESHOLD = "manual_threshold"


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
    auto_detector_requested = pyqtSignal()
    clear_selected_annotation_requested = pyqtSignal()
    roi_toggle_requested = pyqtSignal()
    roi_clear_requested = pyqtSignal()
    # Emitted when the user flips the Annotate / Manual Threshold mode
    # switcher. Carries the new mode as one of the MODE_* constants.
    mode_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the AnnotationControlPanel."""
        super().__init__(parent)
        # Fixed width keeps the window stable when the mode switcher swaps
        # Annotate vs Manual Threshold content (different intrinsic widths).
        self.setFixedWidth(340)
        self.setup_ui()

    def setup_ui(self) -> None:
        """Set up the user interface components."""
        layout = QVBoxLayout()

        self.eye_selector = EyeSelector()
        self.eye_selector.eye_changed.connect(self.eye_changed.emit)
        layout.addWidget(self.eye_selector)

        # Mode switcher: two exclusive checkable buttons act as a segmented
        # control. Annotate mode shows ROI + Annotation Types; Manual
        # Threshold mode shows the threshold tuning panel. Default Annotate.
        self.mode_annotate_button = MaterialButton("Annotate")
        self.mode_annotate_button.setCheckable(True)
        self.mode_annotate_button.setChecked(True)
        self.mode_manual_threshold_button = MaterialButton("Manual Threshold")
        self.mode_manual_threshold_button.setCheckable(True)
        self.mode_button_group = QButtonGroup(self)
        self.mode_button_group.setExclusive(True)
        self.mode_button_group.addButton(self.mode_annotate_button)
        self.mode_button_group.addButton(self.mode_manual_threshold_button)
        self.mode_annotate_button.toggled.connect(
            lambda checked: checked and self._apply_mode(MODE_ANNOTATE, emit=True),
        )
        self.mode_manual_threshold_button.toggled.connect(
            lambda checked: checked and self._apply_mode(MODE_MANUAL_THRESHOLD, emit=True),
        )
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_annotate_button)
        mode_row.addWidget(self.mode_manual_threshold_button)
        layout.addLayout(mode_row)

        # Mode-specific content lives in a QStackedWidget so swapping pages
        # doesn't change the panel's total height and Clear All stays put.
        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self._build_annotate_page())
        self.mode_stack.addWidget(self._build_manual_threshold_page())
        layout.addWidget(self.mode_stack)

        layout.addStretch(1)

        self.clear_all_button = MaterialButton("Clear All")
        self.clear_all_button.clicked.connect(self.clear_all_requested.emit)
        layout.addWidget(self.clear_all_button)

        self.setLayout(layout)

        self._current_mode = MODE_ANNOTATE
        self._apply_mode(MODE_ANNOTATE, emit=False)

    def _build_annotate_page(self) -> QWidget:
        """Page shown in Annotate mode: ROI buttons + manual annotation type groups."""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        roi_row = QHBoxLayout()
        self.roi_toggle_button = MaterialButton("ROI Mode")
        self.roi_toggle_button.setCheckable(True)
        self.roi_toggle_button.clicked.connect(self.roi_toggle_requested.emit)
        roi_row.addWidget(self.roi_toggle_button)
        self.roi_clear_button = MaterialButton("Clear ROI")
        self.roi_clear_button.clicked.connect(self.roi_clear_requested.emit)
        roi_row.addWidget(self.roi_clear_button)
        layout.addLayout(roi_row)

        self.annotation_types_title = QLabel("Annotation Types")
        self.annotation_types_title.setStyleSheet(
            """
            QLabel {
                font-size: 16px;
                font-weight: bold;
                color: #00bcd4;
                padding: 10px 0;
            }
        """
        )
        layout.addWidget(self.annotation_types_title)

        self.pupil_group = AnnotationGroup("Pupil", has_fit=True, has_auto_detector=True)
        self.pupil_group.selected.connect(lambda: self.annotation_changed.emit("pupil"))
        self.pupil_group.fit_requested.connect(self.on_fit_requested)
        self.pupil_group.clear_requested.connect(self.clear_pupil_requested.emit)
        self.pupil_group.auto_detector_requested.connect(self.auto_detector_requested.emit)
        self.pupil_group.set_checked(True)

        self.iris_group = AnnotationGroup("Iris", has_fit=True, has_auto_detector=True)
        self.iris_group.selected.connect(lambda: self.annotation_changed.emit("iris"))
        self.iris_group.fit_requested.connect(self.on_fit_requested)
        self.iris_group.clear_requested.connect(self.clear_iris_requested.emit)
        self.iris_group.auto_detector_requested.connect(self.auto_detector_requested.emit)

        self.eyelid_group = AnnotationGroup("Eyelid Contour", has_fit=False, has_auto_detector=True)
        self.eyelid_group.selected.connect(lambda: self.annotation_changed.emit("eyelid_contour"))
        self.eyelid_group.clear_requested.connect(self.clear_eyelid_points_requested.emit)
        self.eyelid_group.auto_detector_requested.connect(self.auto_detector_requested.emit)

        self.glint_group = AnnotationGroup("Glint", has_fit=False, has_auto_detector=True)
        self.glint_group.selected.connect(lambda: self.annotation_changed.emit("glint"))
        self.glint_group.clear_requested.connect(self.clear_glint_points_requested.emit)
        self.glint_group.auto_detector_requested.connect(self.auto_detector_requested.emit)

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
        page.setLayout(layout)
        return page

    def _build_manual_threshold_page(self) -> QWidget:
        """Page shown in Manual Threshold mode: live-tuning controls."""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.manual_threshold_panel = ManualThresholdPanel()
        layout.addWidget(self.manual_threshold_panel)
        layout.addStretch(1)
        page.setLayout(layout)
        return page

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

    def set_single_eye_mode(self, enabled: bool) -> None:
        """Reflect single-eye mode in the eye selector radio."""
        self.eye_selector.set_current_eye("single" if enabled else "left")

    def current_mode(self) -> str:
        """Return the current mode (``MODE_ANNOTATE`` or ``MODE_MANUAL_THRESHOLD``)."""
        return self._current_mode

    def _apply_mode(self, mode: str, *, emit: bool) -> None:
        """Swap the stacked page for the given mode; emit ``mode_changed`` when requested."""
        self._current_mode = mode
        self.mode_stack.setCurrentIndex(0 if mode == MODE_ANNOTATE else 1)
        if emit:
            self.mode_changed.emit(mode)
