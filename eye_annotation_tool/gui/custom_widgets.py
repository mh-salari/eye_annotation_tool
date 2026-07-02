"""Custom widget components for the application."""

import qtawesome as qta
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .theme import theme


class MaterialButton(QPushButton):
    """Project's standard push button."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)


class AnnotationGroup(QWidget):
    """Manual-mode controls for one annotation kind.

    Embedded inside a :class:`DetectorCard` when the card is in Manual,
    so the surrounding card already shows the kind title. The widget
    here only carries the Add-points toggle and the Fit / Clear
    buttons.
    """

    points_active_toggled = pyqtSignal(bool)
    delete_active_toggled = pyqtSignal(bool)
    fit_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    params_changed = pyqtSignal()  # manual fit mode / centre method / harmonics changed
    max_points_changed = pyqtSignal(int)  # point-cap kinds: per-project max-points value changed

    def __init__(
        self,
        title: str,  # noqa: ARG002 - kept for API compatibility; the card owns the title
        has_fit: bool = True,
        center_methods: tuple[str, ...] = (),
        point_cap: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """Build the click-active radio, fit-mode controls, and Fit / Clear row.

        ``center_methods`` lists the smooth-curve centre estimators to offer
        (empty for kinds without an ellipse fit, where the mode controls hide).
        ``point_cap`` adds a max-points spinbox for the point-only kinds whose
        manual annotation is a small, fixed set of reflections.
        """
        super().__init__(parent)
        self.has_fit = has_fit
        self.point_cap = point_cap
        self._center_methods = tuple(center_methods)
        self.setup_ui()

    def setup_ui(self) -> None:
        """Set up the radio, fit-mode controls, and button row."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # Two compact mode toggles: place points (clicks add/move) and delete
        # points (a click removes the nearest point). Both mirror the ROI button:
        # checkable and mutually exclusive with every other ROI / Add / delete
        # toggle (the controller enforces that), cleared by Escape.
        mode_row = QHBoxLayout()
        mode_row.setSpacing(4)
        self.points_button = QPushButton(qta.icon("mdi6.vector-point-plus", color=theme.color("icon")), " add points")
        self.points_button.setCheckable(True)
        self.points_button.setToolTip("Add points: click to place, drag to move")
        self.points_button.toggled.connect(self.points_active_toggled.emit)
        mode_row.addWidget(self.points_button)
        self.delete_button = QPushButton(qta.icon("mdi6.trash-can-outline", color=theme.color("icon")), "")
        self.delete_button.setCheckable(True)
        self.delete_button.setToolTip("Delete points: click a point to remove it")
        # When armed, the destructive mode reads red rather than the usual accent.
        self.delete_button.setStyleSheet("QPushButton:checked { background-color: #c0392b; }")
        self.delete_button.toggled.connect(self.delete_active_toggled.emit)
        mode_row.addWidget(self.delete_button)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        if self.has_fit:
            self._build_fit_mode_controls(layout)
        if self.point_cap:
            self._build_point_cap_control(layout)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(4)

        if self.has_fit:
            self.fit_button = QPushButton(qta.icon("mdi6.vector-ellipse", color=theme.color("icon")), "")
            self.fit_button.setToolTip("Fit")
            self.fit_button.clicked.connect(self.fit_requested.emit)
            button_layout.addWidget(self.fit_button)

        self.clear_button = QPushButton(qta.icon("mdi6.eraser", color=theme.color("icon")), "")
        self.clear_button.setToolTip("Clear")
        self.clear_button.clicked.connect(self.clear_requested.emit)
        button_layout.addWidget(self.clear_button)

        button_layout.addStretch()

        layout.addLayout(button_layout)
        self.setLayout(layout)

    # Smoothness slider (0..100) maps to the spline penalty; squared for fine
    # control near 0, scaled so the top of the slider is heavily smoothed.
    _SMOOTH_SCALE = 0.02

    def _build_fit_mode_controls(self, layout: QVBoxLayout) -> None:
        """Mode selector (Ellipse | Smooth curve) plus the smooth-curve centre + smoothness."""
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(4)
        mode_row.addWidget(QLabel("fit"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Ellipse", "ellipse")
        self.mode_combo.addItem("Smooth curve", "smooth")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_combo, 1)
        layout.addLayout(mode_row)

        # Smooth-curve-only row: centre estimator + smoothness slider
        # (0 = through every point, higher = smoother).
        self._smooth_row = QWidget()
        sr = QVBoxLayout(self._smooth_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(2)
        self.center_combo = QComboBox()
        for method in self._center_methods:
            self.center_combo.addItem(method, method)
        self.center_combo.currentIndexChanged.connect(lambda _i: self.params_changed.emit())
        sr.addWidget(self.center_combo)
        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(0, 0, 0, 0)
        slider_row.setSpacing(4)
        slider_row.addWidget(QLabel("smooth"))
        self.smooth_slider = QSlider(Qt.Horizontal)
        self.smooth_slider.setRange(0, 100)
        self.smooth_slider.setValue(0)
        self.smooth_slider.valueChanged.connect(lambda _v: self.params_changed.emit())
        slider_row.addWidget(self.smooth_slider, 1)
        sr.addLayout(slider_row)
        layout.addWidget(self._smooth_row)
        self._smooth_row.setVisible(False)

    def _on_mode_changed(self, _index: int) -> None:
        self._smooth_row.setVisible(self.mode_combo.currentData() == "smooth")
        self.params_changed.emit()

    def manual_params(self) -> dict:
        """Return the manual fit settings (empty for kinds without an ellipse fit)."""
        if not self.has_fit:
            return {}
        frac = self.smooth_slider.value() / 100.0
        return {
            "mode": self.mode_combo.currentData(),
            "center_method": self.center_combo.currentData(),
            "smoothness": frac * frac * self._SMOOTH_SCALE,
        }

    def set_manual_params(self, params: dict) -> None:
        """Apply saved manual fit settings without re-emitting ``params_changed``."""
        if not self.has_fit:
            return
        for widget in (self.mode_combo, self.center_combo, self.smooth_slider):
            widget.blockSignals(True)
        mode_idx = self.mode_combo.findData(params.get("mode", "ellipse"))
        self.mode_combo.setCurrentIndex(max(mode_idx, 0))
        center_idx = self.center_combo.findData(params.get("center_method"))
        if center_idx >= 0:
            self.center_combo.setCurrentIndex(center_idx)
        smoothness = params.get("smoothness")
        if isinstance(smoothness, (int, float)) and smoothness >= 0:
            frac = (max(0.0, smoothness) / self._SMOOTH_SCALE) ** 0.5
            self.smooth_slider.setValue(round(min(1.0, frac) * 100))
        for widget in (self.mode_combo, self.center_combo, self.smooth_slider):
            widget.blockSignals(False)
        self._smooth_row.setVisible(self.mode_combo.currentData() == "smooth")

    # Upper bound for the manual point-cap spinbox; well above the handful of
    # corneal reflections a frame can carry.
    _MAX_POINT_CAP = 20

    def _build_point_cap_control(self, layout: QVBoxLayout) -> None:
        """Spinbox capping how many manual points this kind accepts."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(QLabel("max points"))
        self.max_points_spin = QSpinBox()
        self.max_points_spin.setRange(1, self._MAX_POINT_CAP)
        self.max_points_spin.valueChanged.connect(self.max_points_changed.emit)
        row.addWidget(self.max_points_spin, 1)
        layout.addLayout(row)

    def max_points(self) -> int:
        """Return the manual point cap (point-cap kinds only)."""
        return self.max_points_spin.value()

    def set_max_points(self, value: int) -> None:
        """Set the manual point cap without emitting ``max_points_changed``."""
        self.max_points_spin.blockSignals(True)
        self.max_points_spin.setValue(int(value))
        self.max_points_spin.blockSignals(False)

    def is_checked(self) -> bool:
        """Return whether point-adding is active for this kind."""
        return self.points_button.isChecked()

    def set_checked(self, checked: bool) -> None:
        """Set the Add-points toggle without emitting (controller-driven sync)."""
        self.points_button.blockSignals(True)
        self.points_button.setChecked(bool(checked))
        self.points_button.blockSignals(False)

    def set_delete_checked(self, checked: bool) -> None:
        """Set the delete-points toggle without emitting (controller-driven sync)."""
        self.delete_button.blockSignals(True)
        self.delete_button.setChecked(bool(checked))
        self.delete_button.blockSignals(False)


class EyeSelector(QGroupBox):
    """Selector for binocular vs monocular images and active eye in binocular mode.

    A **Binocular** checkbox toggles whether the image contains two eyes.
    When checked, a Left / Right radio pair is shown so the user can pick
    which eye the canvas + manual + auto-detect workflow is currently
    operating on. When unchecked, the image is treated as a single eye
    with no left/right distinction — the radios are hidden.

    Two independent signals carry the selector's state outward:

    - :pyattr:`binocular_toggled` — emitted when the checkbox flips.
    - :pyattr:`eye_changed` — emitted when the Left / Right radio
      changes (only meaningful when binocular).
    """

    eye_changed = pyqtSignal(str)
    binocular_toggled = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the EyeSelector."""
        super().__init__("Eye Selection", parent)
        self.setup_ui()

    def setup_ui(self) -> None:
        """Set up the user interface for the eye selector."""
        layout = QVBoxLayout()
        layout.setSpacing(4)

        self.binocular_check = QCheckBox("Binocular image")
        self.binocular_check.setChecked(True)
        self.binocular_check.toggled.connect(self._on_binocular_toggled)
        layout.addWidget(self.binocular_check)

        self.eye_row = QWidget()
        eye_layout = QHBoxLayout(self.eye_row)
        eye_layout.setContentsMargins(0, 0, 0, 0)
        eye_layout.setSpacing(10)
        self.left_eye_radio = QRadioButton("Left Eye")
        self.right_eye_radio = QRadioButton("Right Eye")
        self.left_eye_radio.setChecked(True)
        self.button_group = QButtonGroup(self)
        self.button_group.addButton(self.left_eye_radio)
        self.button_group.addButton(self.right_eye_radio)
        self.left_eye_radio.clicked.connect(lambda: self.eye_changed.emit("left"))
        self.right_eye_radio.clicked.connect(lambda: self.eye_changed.emit("right"))
        eye_layout.addWidget(self.left_eye_radio)
        eye_layout.addWidget(self.right_eye_radio)
        eye_layout.addStretch()
        layout.addWidget(self.eye_row)

        self.setLayout(layout)

    def _on_binocular_toggled(self, checked: bool) -> None:
        """Show / hide the Left-Right radios and forward the toggle."""
        self.eye_row.setVisible(checked)
        self.binocular_toggled.emit(checked)

    def is_binocular(self) -> bool:
        """Return ``True`` when the user has marked the image as binocular."""
        return self.binocular_check.isChecked()

    def set_binocular(self, enabled: bool) -> None:
        """Set the binocular checkbox without emitting ``binocular_toggled``."""
        self.binocular_check.blockSignals(True)
        self.binocular_check.setChecked(enabled)
        self.binocular_check.blockSignals(False)
        self.eye_row.setVisible(enabled)

    def get_current_eye(self) -> str:
        """Return ``"left"`` or ``"right"`` based on the active radio.

        The return value is only meaningful when :meth:`is_binocular` is
        ``True``; in monocular mode the caller should use the image-wide
        flat data store instead.
        """
        return "left" if self.left_eye_radio.isChecked() else "right"

    def set_current_eye(self, eye: str) -> None:
        """Set the currently selected eye (``"left"`` / ``"right"``) without emitting."""
        radio = self.right_eye_radio if eye == "right" else self.left_eye_radio
        radio.blockSignals(True)
        radio.setChecked(True)
        radio.blockSignals(False)


class GateRow(QWidget):
    """One-line panel control for a 50-100 % shape-quality gate.

    Wraps a labelled :class:`QCheckBox` (enable/disable the gate),
    a horizontal :class:`QSlider` and a linked :class:`QSpinBox`.
    The slider and spinbox track each other; the spinbox always
    displays the value with a ``%`` suffix. Two signals carry user
    interaction outward:

    - :pyattr:`toggled(bool)` — checkbox flipped; the slider / spinbox
      are greyed / enabled to match before this fires.
    - :pyattr:`pct_changed(int)` — slider (or linked spinbox) value
      changed.

    Used for the Min ellipse fit + Min roundness rows in the Threshold
    Pupil and Threshold Glint plugin panels.
    """

    toggled = pyqtSignal(bool)
    pct_changed = pyqtSignal(int)

    def __init__(
        self,
        label: str,
        *,
        initial_enabled: bool,
        initial_pct: int,
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """Build the row pre-populated with ``(initial_enabled, initial_pct)``."""
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.check = QCheckBox(label)
        self.check.setChecked(initial_enabled)
        self.check.setToolTip(tooltip)
        self.check.setMinimumWidth(140)
        self.check.toggled.connect(self._on_toggled)
        layout.addWidget(self.check)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(50, 100)
        self.slider.setValue(initial_pct)
        self.slider.setEnabled(initial_enabled)
        self.slider.setToolTip(tooltip)
        self.spin = QSpinBox()
        self.spin.setRange(50, 100)
        self.spin.setValue(initial_pct)
        self.spin.setSuffix(" %")
        self.spin.setEnabled(initial_enabled)
        self.spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin.setMinimumWidth(60)
        self.spin.setMaximumWidth(80)
        self.spin.setToolTip(tooltip)
        # Slider drives the spinbox and vice versa; only the slider's
        # valueChanged is forwarded to ``pct_changed`` so listeners see
        # a single emission per user action.
        self.slider.valueChanged.connect(self.spin.setValue)
        self.spin.valueChanged.connect(self.slider.setValue)
        self.slider.valueChanged.connect(self.pct_changed.emit)
        layout.addWidget(self.slider)
        layout.addWidget(self.spin)

    def _on_toggled(self, checked: bool) -> None:
        self.slider.setEnabled(checked)
        self.spin.setEnabled(checked)
        self.toggled.emit(checked)

    def is_checked(self) -> bool:
        """Return the gate's enable flag."""
        return self.check.isChecked()

    def pct(self) -> int:
        """Return the gate's current percentage value."""
        return self.spin.value()

    def set_state(self, *, enabled: bool, pct: int) -> None:
        """Silently restore the row to ``(enabled, pct)`` for ``set_params`` round-trips."""
        widgets = (self.check, self.slider, self.spin)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.check.setChecked(enabled)
            self.slider.setEnabled(enabled)
            self.spin.setEnabled(enabled)
            self.slider.setValue(pct)
            self.spin.setValue(pct)
        finally:
            for w in widgets:
                w.blockSignals(False)
