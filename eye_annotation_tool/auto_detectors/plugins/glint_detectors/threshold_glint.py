"""Threshold-based glint detector plugin.

Detects bright glints inside a circular pupil-centred disk. Requires the
pupil target so the search radius can be expressed as a multiple of the
pupil radius — without a pupil result this plugin produces nothing.

The panel exposes:

  - bright-threshold slider,
  - search-radius factor slider (multiple of pupil radius),
  - centre-method dropdown (same four methods the pupil plugin offers),
  - expected number of glints (rig-LED count),
  - optional max-area cap in pixels (skin / eyelid bleed-through guard),
  - four half-plane filter toggles (drop above / below / left / right
    of the pupil centre),
  - "coalesce into one" toggle (only meaningful when target == 1),
  - "split widest blob" toggle (4-LED rig: two LEDs merged into one
    bright spot).

All refiners are off / neutral by default so the panel works out of
the box for a single-LED rig at the pgd defaults.
"""

import numpy as np
from pupil_glint_detector import detect_glints
from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from eye_annotation_tool.auto_detectors.plugin_interface import DetectorPlugin
from eye_annotation_tool.gui.custom_widgets import MaterialButton

# Overlay palette for the Threshold Glint plugin. Saturated red reads
# on bright glint highlights and on the surrounding iris; the mask fill
# uses magenta so it contrasts with the pupil mask cyan.
GLINT_COLOR = QColor(255, 40, 40, 255)
ROI_COLOR = GLINT_COLOR
MASK_COLOR = QColor(255, 0, 200, 110)

# Same shape as the pupil plugin's centre-method dropdown — the four
# methods come from pgd's _contour_center helper, which both plugins
# pass into their detector calls via the ``glint_center_method`` /
# ``pupil_center_method`` parameter.
CENTER_METHODS: tuple[tuple[str, str], ...] = (
    ("Convex hull centroid", "convex_hull_centroid"),
    ("Center of mass", "center_of_mass"),
    ("Ellipse fit center", "ellipse_fit_center"),
    ("Min area rect", "min_area_rect_center"),
)

# Slider scale for the float ``search_radius_factor`` knob: the slider
# carries an int in [1, 50] and we divide by ``RADIUS_FACTOR_SCALE`` to
# get the actual multiplier in (0.1, 5.0].
RADIUS_FACTOR_SCALE = 10

DEFAULTS: dict = {
    "glint_threshold": 240,
    "search_radius_factor": 2.0,
    "glints_target": 1,
    "glint_center_method": "min_area_rect_center",
    # ``0`` means "no area cap"; pgd interprets ``None`` the same way
    # and the plugin maps 0 → None when calling.
    "max_area_px": 0,
    "keep_above": True,
    "keep_below": True,
    "keep_left": True,
    "keep_right": True,
    "filter_margin_px": 5,
    "coalesce_into_one": True,
    "split_widest_for_target": False,
    # An ``(x, y, w, h)`` tuple set by the canvas drag handler, or None
    # when no ROI is active. Intersects with the pupil-centred search
    # disk in pgd.detect_glints.
    "glint_roi": None,
}


class _ThresholdGlintPanel(QGroupBox):
    """Right-panel widget for the Threshold Glint plugin."""

    params_changed = pyqtSignal(dict)
    # Emitted when the user toggles the "Show mask" checkbox. MainWindow
    # forwards to the image viewer so the threshold mask paint path is
    # gated independently per plugin.
    show_mask_toggled = pyqtSignal(bool)
    # Emitted when the user toggles the "Glint ROI" button. The image
    # viewer puts itself in drag-edit mode for the glint ROI rectangle
    # while this is True.
    roi_edit_requested = pyqtSignal(bool)
    # Emitted when the user clicks Clear next to the ROI button — the
    # canvas drops the rectangle and the plugin re-runs without it.
    clear_roi_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the panel widgets and seed them with :data:`DEFAULTS`."""
        super().__init__("Threshold Glint", parent)
        self._params: dict = dict(DEFAULTS)
        self._build_ui()

    # ----- widget construction -----

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        layout.addLayout(self._build_threshold_row())
        layout.addLayout(self._build_radius_row())
        layout.addLayout(self._build_glints_target_row())
        layout.addLayout(self._build_method_row())
        layout.addLayout(self._build_max_area_row())
        layout.addLayout(self._build_keep_row())
        layout.addLayout(self._build_filter_margin_row())
        layout.addLayout(self._build_flags_row())
        layout.addLayout(self._build_roi_row())

        self.show_mask_check = QCheckBox("Show mask")
        self.show_mask_check.setChecked(False)
        self.show_mask_check.toggled.connect(self.show_mask_toggled.emit)
        layout.addWidget(self.show_mask_check)

        self.setLayout(layout)

    def _build_roi_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.roi_button = MaterialButton("Glint ROI")
        self.roi_button.setCheckable(True)
        self.roi_button.toggled.connect(self.roi_edit_requested.emit)
        self.clear_roi_button = MaterialButton("Clear")
        self.clear_roi_button.clicked.connect(self.clear_roi_requested.emit)
        row.addWidget(self.roi_button)
        row.addWidget(self.clear_roi_button)
        return row

    def _build_threshold_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Threshold:")
        label.setMinimumWidth(110)
        row.addWidget(label)
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(0, 255)
        self.threshold_slider.setValue(self._params["glint_threshold"])
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 255)
        self.threshold_spin.setValue(self._params["glint_threshold"])
        self.threshold_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.threshold_spin.setMinimumWidth(50)
        self.threshold_spin.setMaximumWidth(70)
        self.threshold_slider.valueChanged.connect(self.threshold_spin.setValue)
        self.threshold_spin.valueChanged.connect(self.threshold_slider.setValue)
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        row.addWidget(self.threshold_slider)
        row.addWidget(self.threshold_spin)
        return row

    def _build_radius_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Search radius:")
        label.setMinimumWidth(110)
        label.setToolTip(
            "Maximum distance from the pupil centre at which a bright pixel can "
            "be considered for glint detection, expressed as a multiple of the "
            "detected pupil radius. 2.0 = twice the pupil's radius.",
        )
        row.addWidget(label)
        initial_factor = float(self._params["search_radius_factor"])
        initial_slider = round(initial_factor * RADIUS_FACTOR_SCALE)
        self.radius_slider = QSlider(Qt.Horizontal)
        self.radius_slider.setRange(1, 5 * RADIUS_FACTOR_SCALE)
        self.radius_slider.setValue(initial_slider)
        self.radius_spin = QDoubleSpinBox()
        self.radius_spin.setRange(0.1, 5.0)
        self.radius_spin.setSingleStep(0.1)
        self.radius_spin.setDecimals(1)
        # Suffix nails the unit at the value site: "2.0 x pupil_r" reads
        # unambiguously as "two times the pupil radius".
        self.radius_spin.setSuffix(" x pupil_r")
        self.radius_spin.setValue(initial_factor)
        self.radius_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.radius_spin.setMinimumWidth(80)
        self.radius_spin.setMaximumWidth(110)
        self.radius_slider.valueChanged.connect(self._on_radius_slider_changed)
        self.radius_spin.valueChanged.connect(self._on_radius_spin_changed)
        row.addWidget(self.radius_slider)
        row.addWidget(self.radius_spin)
        return row

    def _build_glints_target_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Glints:")
        label.setMinimumWidth(110)
        row.addWidget(label)
        self.glints_target_spin = QSpinBox()
        self.glints_target_spin.setRange(1, 8)
        self.glints_target_spin.setValue(self._params["glints_target"])
        self.glints_target_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.glints_target_spin.setMinimumWidth(50)
        self.glints_target_spin.setMaximumWidth(70)
        self.glints_target_spin.valueChanged.connect(self._on_glints_target_changed)
        row.addWidget(self.glints_target_spin)
        row.addStretch()
        return row

    def _build_method_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Center:")
        label.setMinimumWidth(110)
        row.addWidget(label)
        self.method_combo = QComboBox()
        for display, key in CENTER_METHODS:
            self.method_combo.addItem(display, key)
        initial_idx = next(
            (i for i, (_, k) in enumerate(CENTER_METHODS) if k == self._params["glint_center_method"]),
            0,
        )
        self.method_combo.setCurrentIndex(initial_idx)
        self.method_combo.currentIndexChanged.connect(self._on_method_changed)
        row.addWidget(self.method_combo)
        row.addStretch()
        return row

    def _build_max_area_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Max area (px):")
        label.setMinimumWidth(110)
        row.addWidget(label)
        self.max_area_spin = QSpinBox()
        # 0 means "no cap". Upper bound covers a generous fraction of a
        # 4K eye-tracker frame so the user can effectively turn the
        # filter off by sliding right.
        self.max_area_spin.setRange(0, 100000)
        self.max_area_spin.setSpecialValueText("off")
        self.max_area_spin.setValue(self._params["max_area_px"])
        self.max_area_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.max_area_spin.setMinimumWidth(60)
        self.max_area_spin.setMaximumWidth(90)
        self.max_area_spin.valueChanged.connect(self._on_max_area_changed)
        row.addWidget(self.max_area_spin)
        row.addStretch()
        return row

    def _build_keep_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Keep:")
        label.setMinimumWidth(110)
        row.addWidget(label)
        self.keep_above_box = self._make_keep_check("above", self._params["keep_above"])
        self.keep_below_box = self._make_keep_check("below", self._params["keep_below"])
        self.keep_left_box = self._make_keep_check("left", self._params["keep_left"])
        self.keep_right_box = self._make_keep_check("right", self._params["keep_right"])
        row.addWidget(self.keep_above_box)
        row.addWidget(self.keep_below_box)
        row.addWidget(self.keep_left_box)
        row.addWidget(self.keep_right_box)
        row.addStretch()
        return row

    def _make_keep_check(self, half: str, initial: bool) -> QCheckBox:
        box = QCheckBox(half)
        box.setChecked(initial)
        box.toggled.connect(lambda checked, h=half: self._on_keep_changed(h, checked))
        return box

    def _build_filter_margin_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Filter margin (px):")
        label.setMinimumWidth(110)
        label.setToolTip(
            "When only one half of an axis is kept (e.g. only 'below'), a "
            "glint whose centroid sits within this many pixels past the "
            "pupil-centre line on the opposite side still passes the filter.",
        )
        row.addWidget(label)
        self.filter_margin_spin = QSpinBox()
        self.filter_margin_spin.setRange(0, 100)
        self.filter_margin_spin.setValue(self._params["filter_margin_px"])
        self.filter_margin_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.filter_margin_spin.setMinimumWidth(60)
        self.filter_margin_spin.setMaximumWidth(90)
        self.filter_margin_spin.valueChanged.connect(self._on_filter_margin_changed)
        row.addWidget(self.filter_margin_spin)
        row.addStretch()
        return row

    def _build_flags_row(self) -> QVBoxLayout:
        # Two boolean refiners stacked vertically — each gets its own
        # row so the label has space to read.
        wrap = QVBoxLayout()
        self.coalesce_check = QCheckBox("Coalesce into one (target == 1)")
        self.coalesce_check.setChecked(self._params["coalesce_into_one"])
        self.coalesce_check.toggled.connect(self._on_coalesce_changed)
        wrap.addWidget(self.coalesce_check)
        self.split_widest_check = QCheckBox("Split widest blob when one is missing")
        self.split_widest_check.setChecked(self._params["split_widest_for_target"])
        self.split_widest_check.toggled.connect(self._on_split_widest_changed)
        wrap.addWidget(self.split_widest_check)
        return wrap

    # ----- widget event handlers -----

    def _on_threshold_changed(self, value: int) -> None:
        self._params["glint_threshold"] = int(value)
        self.params_changed.emit(dict(self._params))

    def _on_radius_slider_changed(self, value: int) -> None:
        # Slider drives the spinbox; the spinbox handler does the
        # params update and emit, so this side stays signal-free.
        factor = value / RADIUS_FACTOR_SCALE
        self.radius_spin.blockSignals(True)
        self.radius_spin.setValue(factor)
        self.radius_spin.blockSignals(False)
        self._params["search_radius_factor"] = float(factor)
        self.params_changed.emit(dict(self._params))

    def _on_radius_spin_changed(self, value: float) -> None:
        slider_val = round(value * RADIUS_FACTOR_SCALE)
        self.radius_slider.blockSignals(True)
        self.radius_slider.setValue(slider_val)
        self.radius_slider.blockSignals(False)
        self._params["search_radius_factor"] = float(value)
        self.params_changed.emit(dict(self._params))

    def _on_glints_target_changed(self, value: int) -> None:
        self._params["glints_target"] = int(value)
        self.params_changed.emit(dict(self._params))

    def _on_method_changed(self, idx: int) -> None:
        self._params["glint_center_method"] = self.method_combo.itemData(idx)
        self.params_changed.emit(dict(self._params))

    def _on_max_area_changed(self, value: int) -> None:
        self._params["max_area_px"] = int(value)
        self.params_changed.emit(dict(self._params))

    def _on_keep_changed(self, half: str, checked: bool) -> None:
        self._params[f"keep_{half}"] = bool(checked)
        self.params_changed.emit(dict(self._params))

    def _on_filter_margin_changed(self, value: int) -> None:
        self._params["filter_margin_px"] = int(value)
        self.params_changed.emit(dict(self._params))

    def _on_coalesce_changed(self, checked: bool) -> None:
        self._params["coalesce_into_one"] = bool(checked)
        self.params_changed.emit(dict(self._params))

    def _on_split_widest_changed(self, checked: bool) -> None:
        self._params["split_widest_for_target"] = bool(checked)
        self.params_changed.emit(dict(self._params))

    # ----- contract surface consumed by the orchestrator -----

    def current_params(self) -> dict:
        """Return a copy of the panel's current parameter dict."""
        return dict(self._params)

    def set_params(self, params: dict) -> None:
        """Populate the widgets from ``params`` without emitting ``params_changed``.

        Each widget's value-change signal is blocked individually so
        the slider/spin mirrors don't ricochet. Unknown keys in
        ``params`` are ignored.
        """
        widgets = (
            self.threshold_slider,
            self.threshold_spin,
            self.radius_slider,
            self.radius_spin,
            self.glints_target_spin,
            self.method_combo,
            self.max_area_spin,
            self.keep_above_box,
            self.keep_below_box,
            self.keep_left_box,
            self.keep_right_box,
            self.filter_margin_spin,
            self.coalesce_check,
            self.split_widest_check,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            if "glint_threshold" in params:
                value = int(params["glint_threshold"])
                self.threshold_slider.setValue(value)
                self.threshold_spin.setValue(value)
                self._params["glint_threshold"] = value
            if "search_radius_factor" in params:
                factor = float(params["search_radius_factor"])
                self.radius_slider.setValue(round(factor * RADIUS_FACTOR_SCALE))
                self.radius_spin.setValue(factor)
                self._params["search_radius_factor"] = factor
            if "glints_target" in params:
                self.glints_target_spin.setValue(int(params["glints_target"]))
                self._params["glints_target"] = int(params["glints_target"])
            if "glint_center_method" in params:
                method = params["glint_center_method"]
                idx = next((i for i, (_, k) in enumerate(CENTER_METHODS) if k == method), -1)
                if idx >= 0:
                    self.method_combo.setCurrentIndex(idx)
                    self._params["glint_center_method"] = method
            if "max_area_px" in params:
                self.max_area_spin.setValue(int(params["max_area_px"]))
                self._params["max_area_px"] = int(params["max_area_px"])
            for half, box in (
                ("above", self.keep_above_box),
                ("below", self.keep_below_box),
                ("left", self.keep_left_box),
                ("right", self.keep_right_box),
            ):
                key = f"keep_{half}"
                if key in params:
                    box.setChecked(bool(params[key]))
                    self._params[key] = bool(params[key])
            if "filter_margin_px" in params:
                self.filter_margin_spin.setValue(int(params["filter_margin_px"]))
                self._params["filter_margin_px"] = int(params["filter_margin_px"])
            if "coalesce_into_one" in params:
                self.coalesce_check.setChecked(bool(params["coalesce_into_one"]))
                self._params["coalesce_into_one"] = bool(params["coalesce_into_one"])
            if "split_widest_for_target" in params:
                self.split_widest_check.setChecked(bool(params["split_widest_for_target"]))
                self._params["split_widest_for_target"] = bool(params["split_widest_for_target"])
            if "glint_roi" in params:
                self._params["glint_roi"] = params["glint_roi"]
        finally:
            for w in widgets:
                w.blockSignals(False)

    def set_glint_roi(self, roi: tuple | None) -> None:
        """Push a canvas-edited ROI into the params dict and emit ``params_changed``.

        Called by MainWindow when the image viewer finishes a drag on
        the glint ROI rectangle. Triggers the usual debounce → run_one
        cycle so the new search region applies immediately.
        """
        self._params["glint_roi"] = tuple(roi) if roi is not None else None
        self.params_changed.emit(dict(self._params))


class ThresholdGlint(DetectorPlugin):
    """Threshold-based glint detection backed by ``pupil-glint-detector``."""

    name = "threshold_glint"
    target = "glint"
    requires = ("pupil",)
    live = True
    overlay_z_order = 10
    roi_color = ROI_COLOR
    mask_color = MASK_COLOR

    @classmethod
    def default_params(cls) -> dict:
        """Return a fresh copy of this plugin's default parameter values."""
        return dict(DEFAULTS)

    def make_panel(self, parent: QWidget | None = None) -> QWidget:
        """Build the Qt parameter panel for this plugin."""
        return _ThresholdGlintPanel(parent)

    def detect(
        self,
        image: np.ndarray,
        params: dict,
        shared_results: dict,
    ) -> dict | None:
        """Run threshold glint detection given the pupil result.

        The pupil's centre + ellipse arrive via ``shared_results["pupil"]``
        — the orchestrator guarantees the entry exists because
        ``requires=("pupil",)``. Pupil radius is taken as half the
        longer ellipse axis.
        """
        pupil = shared_results["pupil"]
        pupil_center = tuple(pupil["center"])
        ew, eh = pupil["ellipse"]["size"]
        pupil_radius = max(ew, eh) / 2.0

        max_area = int(params.get("max_area_px", 0)) or None
        glint_roi = params.get("glint_roi")
        result = detect_glints(
            image,
            pupil_center=pupil_center,
            pupil_radius=pupil_radius,
            glint_threshold=int(params["glint_threshold"]),
            search_radius_factor=float(params["search_radius_factor"]),
            glint_roi=tuple(glint_roi) if glint_roi is not None else None,
            glint_center_method=params["glint_center_method"],
            max_area_px=max_area,
            keep_above=bool(params["keep_above"]),
            keep_below=bool(params["keep_below"]),
            keep_left=bool(params["keep_left"]),
            keep_right=bool(params["keep_right"]),
            filter_margin_px=int(params["filter_margin_px"]),
            glints_target=int(params["glints_target"]),
            coalesce_into_one=bool(params["coalesce_into_one"]),
            split_widest_for_target=bool(params["split_widest_for_target"]),
        )
        return {
            "glints": [{"center": [float(g["center"][0]), float(g["center"][1])]} for g in result["glints"]],
            # Transient pixel mask of the bright-and-in-search-disk
            # candidates. Surfaced under the standard ``"mask"`` key the
            # plugin contract uses for the optional "Show mask" overlay;
            # stripped on serialize().
            "mask": result["search_area"],
        }

    def serialize(self, result: dict) -> dict:
        """Reduce a result dict to JSON-friendly types for per-image storage."""
        return {"glints": [{"center": list(g["center"])} for g in result.get("glints", [])]}

    def deserialize(self, blob: dict) -> dict:
        """Reconstruct an in-memory result dict from a stored JSON blob."""
        return {"glints": [{"center": list(g["center"])} for g in blob.get("glints", [])]}

    def translate_for_crop(self, result: dict, dx: float, dy: float) -> dict:
        """Shift every detected glint centre from crop coords to full image."""
        translated = {
            "glints": [{"center": [g["center"][0] + dx, g["center"][1] + dy]} for g in result.get("glints", [])],
        }
        if "mask" in result:
            translated["mask"] = result["mask"]
        return translated

    def draw_overlay(self, painter: QPainter, result: dict, scale: float) -> None:
        """Render each detected glint as a small filled red dot."""
        glints = result.get("glints") or []
        painter.setBrush(GLINT_COLOR)
        painter.setPen(QPen(GLINT_COLOR, 3, Qt.SolidLine))
        for g in glints:
            gx, gy = g["center"]
            painter.drawEllipse(QPointF(gx * scale, gy * scale), 1.5, 1.5)
