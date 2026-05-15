"""Right-panel widget for the Manual Threshold mode.

Exposes the per-image threshold knobs (pupil + glint thresholds, glint margin,
glint count target, glint area ratio, pupil-centre method) as a single
``QGroupBox``. Visibility is controlled externally by the mode switcher in
``AnnotationControlPanel``; ``params_changed`` always fires when any control
moves so callers can rely on the signal independent of UI state.
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .custom_widgets import MaterialButton

PUPIL_CENTER_METHODS = (
    ("Convex hull centroid", "convex_hull_centroid"),
    ("Center of mass", "center_of_mass"),
    ("Ellipse fit center", "ellipse_fit_center"),
)

# Defaults match the docstring defaults on
# ``annotation_app.auto_detectors.algorithms.pupil.detect_pupil_and_glints``
# so opening the panel without prior tuning is a sensible starting point.
DEFAULT_PARAMS = {
    "pupil_threshold": 30,
    "glint_threshold": 240,
    "glint_margin": 10,
    "glints_target": 1,
    "glint_max_area_ratio": 0.1,
    "pupil_center_method": "convex_hull_centroid",
}


class ManualThresholdPanel(QGroupBox):
    """Sliders + spin boxes + method radio for live threshold tuning."""

    params_changed = pyqtSignal(dict)
    # Pupil/Glint ROI mode toggles. Bool payload: True = activate that ROI's
    # drag-edit mode, False = deactivate. Only one ROI active at a time
    # (enforced inside the panel via mutex on toggle).
    pupil_roi_mode_changed = pyqtSignal(bool)
    glint_roi_mode_changed = pyqtSignal(bool)
    clear_pupil_roi_requested = pyqtSignal()
    clear_glint_roi_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialise the panel.

        The panel is always-on once visible; AnnotationControlPanel's mode
        switcher decides whether it is shown. No internal checkbox.
        """
        super().__init__("Manual Threshold", parent)
        self._params = dict(DEFAULT_PARAMS)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QGroupBox {
                border: 1px solid #555;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 10px;
                background-color: #2b2b2b;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #00bcd4;
                font-weight: bold;
            }
            QLabel { color: #e0e0e0; }
            QRadioButton { color: #e0e0e0; }
            QSpinBox, QDoubleSpinBox {
                background-color: #3a3a3a;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 2px 4px;
            }
            """
        )
        layout = QVBoxLayout()

        self.pupil_threshold_slider, self.pupil_threshold_label = self._add_int_slider(
            layout, "Pupil threshold", 0, 255, self._params["pupil_threshold"],
        )
        self.glint_threshold_slider, self.glint_threshold_label = self._add_int_slider(
            layout, "Glint threshold", 0, 255, self._params["glint_threshold"],
        )
        self.glint_margin_slider, self.glint_margin_label = self._add_int_slider(
            layout, "Glint margin", 0, 50, self._params["glint_margin"],
        )

        # Glint count target + area ratio in one row to save space.
        row = QHBoxLayout()
        row.addWidget(QLabel("Glints target:"))
        self.glints_target_spin = QSpinBox()
        self.glints_target_spin.setRange(1, 8)
        self.glints_target_spin.setValue(self._params["glints_target"])
        self.glints_target_spin.valueChanged.connect(self._on_glints_target_changed)
        row.addWidget(self.glints_target_spin)
        row.addSpacing(8)
        row.addWidget(QLabel("Max area ratio:"))
        self.area_ratio_spin = QDoubleSpinBox()
        self.area_ratio_spin.setRange(0.0, 1.0)
        self.area_ratio_spin.setSingleStep(0.01)
        self.area_ratio_spin.setDecimals(2)
        self.area_ratio_spin.setValue(self._params["glint_max_area_ratio"])
        self.area_ratio_spin.valueChanged.connect(self._on_area_ratio_changed)
        row.addWidget(self.area_ratio_spin)
        row.addStretch()
        layout.addLayout(row)

        # Pupil-centre method (3 radio buttons).
        layout.addWidget(QLabel("Pupil center method:"))
        self.method_group = QButtonGroup(self)
        self.method_buttons = {}
        method_row = QHBoxLayout()
        for label, key in PUPIL_CENTER_METHODS:
            btn = QRadioButton(label)
            if key == self._params["pupil_center_method"]:
                btn.setChecked(True)
            btn.toggled.connect(lambda checked, k=key: self._on_method_toggled(checked, k))
            self.method_group.addButton(btn)
            self.method_buttons[key] = btn
            method_row.addWidget(btn)
        method_row.addStretch()
        layout.addLayout(method_row)

        # Pupil ROI / Glint ROI controls. Toggle on a button to drag a
        # rectangle on the image; press Clear to remove it.
        self.pupil_roi_button = MaterialButton("Pupil ROI")
        self.pupil_roi_button.setCheckable(True)
        self.pupil_roi_button.toggled.connect(self._on_pupil_roi_toggled)
        self.clear_pupil_roi_button = MaterialButton("Clear")
        self.clear_pupil_roi_button.clicked.connect(self.clear_pupil_roi_requested.emit)
        pupil_row = QHBoxLayout()
        pupil_row.addWidget(self.pupil_roi_button)
        pupil_row.addWidget(self.clear_pupil_roi_button)
        layout.addLayout(pupil_row)

        self.glint_roi_button = MaterialButton("Glint ROI")
        self.glint_roi_button.setCheckable(True)
        self.glint_roi_button.toggled.connect(self._on_glint_roi_toggled)
        self.clear_glint_roi_button = MaterialButton("Clear")
        self.clear_glint_roi_button.clicked.connect(self.clear_glint_roi_requested.emit)
        glint_row = QHBoxLayout()
        glint_row.addWidget(self.glint_roi_button)
        glint_row.addWidget(self.clear_glint_roi_button)
        layout.addLayout(glint_row)

        self.setLayout(layout)

    def _add_int_slider(
        self,
        parent_layout: QVBoxLayout,
        title: str,
        minimum: int,
        maximum: int,
        initial: int,
    ) -> tuple[QSlider, QLabel]:
        row = QHBoxLayout()
        title_label = QLabel(f"{title}:")
        title_label.setMinimumWidth(120)
        row.addWidget(title_label)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(initial)
        value_label = QLabel(str(initial))
        value_label.setMinimumWidth(32)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider.valueChanged.connect(lambda v, lbl=value_label, t=title: self._on_slider_changed(t, v, lbl))
        row.addWidget(slider)
        row.addWidget(value_label)
        parent_layout.addLayout(row)
        return slider, value_label

    # ----- event handlers -----

    def _on_slider_changed(self, title: str, value: int, value_label: QLabel) -> None:
        value_label.setText(str(value))
        key = {
            "Pupil threshold": "pupil_threshold",
            "Glint threshold": "glint_threshold",
            "Glint margin": "glint_margin",
        }[title]
        self._params[key] = int(value)
        self.params_changed.emit(dict(self._params))

    def _on_glints_target_changed(self, value: int) -> None:
        self._params["glints_target"] = int(value)
        self.params_changed.emit(dict(self._params))

    def _on_area_ratio_changed(self, value: float) -> None:
        self._params["glint_max_area_ratio"] = float(value)
        self.params_changed.emit(dict(self._params))

    def _on_method_toggled(self, checked: bool, key: str) -> None:
        if not checked:
            return
        self._params["pupil_center_method"] = key
        self.params_changed.emit(dict(self._params))

    def _on_pupil_roi_toggled(self, checked: bool) -> None:
        # Mutex with the glint ROI toggle.
        if checked and self.glint_roi_button.isChecked():
            self.glint_roi_button.setChecked(False)
        self.pupil_roi_mode_changed.emit(checked)

    def _on_glint_roi_toggled(self, checked: bool) -> None:
        if checked and self.pupil_roi_button.isChecked():
            self.pupil_roi_button.setChecked(False)
        self.glint_roi_mode_changed.emit(checked)

    def deactivate_roi_buttons(self) -> None:
        """Untoggle both ROI buttons (e.g. when leaving Manual Threshold mode)."""
        self.pupil_roi_button.setChecked(False)
        self.glint_roi_button.setChecked(False)

    def current_params(self) -> dict:
        """Return a copy of the current parameter dict."""
        return dict(self._params)
