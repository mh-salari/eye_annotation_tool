"""Threshold-based pupil detector plugin.

Wraps ``lavan.detect.detect_pupil`` — pupil-only, no implicit
limbus or glint pass. The panel exposes:

  - pupil threshold (slider + linked spinbox),
  - centre-method dropdown (four methods from the underlying algorithm),
  - pupil-ROI toggle + clear (drives canvas drag-edit mode).

The result feeds downstream targets (glint, limbus) via the
orchestrator's ``shared_results`` dict.
"""

import numpy as np
from lavan.detect import detect_pupil
from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from eye_annotation_tool.auto_detectors.plugin_interface import DetectorPlugin
from eye_annotation_tool.gui.custom_widgets import GateRow, MaterialButton

# Overlay palette for the Threshold Pupil plugin. Kept local to the
# plugin so adding a new pupil detector with a different look does not
# need core edits.
ELLIPSE_COLOR = QColor(25, 145, 50, 255)
CENTER_COLOR = QColor(180, 240, 80, 255)
ROI_COLOR = CENTER_COLOR
MASK_COLOR = QColor(50, 180, 80, 70)

# Display label / serialised key for each of the four centre-computation
# methods exposed by lavan.detect. Shared shape with the glint
# plugin, but each plugin keeps its own combo so the user can choose
# pupil and glint independently.
CENTER_METHODS: tuple[tuple[str, str], ...] = (
    ("Convex hull centroid", "convex_hull_centroid"),
    ("Center of mass", "center_of_mass"),
    ("Ellipse fit center", "ellipse_fit_center"),
    ("Min area rect", "min_area_rect_center"),
)

DEFAULTS: dict = {
    "pupil_threshold": 50,
    "pupil_center_method": "convex_hull_centroid",
    # An ``(x, y, w, h)`` tuple set by the canvas drag handler, or None
    # when no ROI is active. Stored in the same params dict as the
    # numeric knobs so a single ``set_params`` call restores everything.
    "pupil_roi": None,
    # Optional shape-quality gates forwarded to lavan.detect.detect_pupil. Each
    # pair is a checkbox + integer percentage (50..100); the plugin
    # divides by 100 before passing the ratio to lavan.detect.
    "min_ellipse_fit_enabled": True,
    "min_ellipse_fit_pct": 80,
    "min_roundness_enabled": False,
    "min_roundness_pct": 70,
}

ELLIPSE_FIT_TOOLTIP = (
    "How well the detected contour fits its own ellipse. 100% = the "
    "contour exactly traces an ellipse. Lower values reject fragmented "
    "or jagged shapes."
)
ROUNDNESS_TOOLTIP = (
    "How circular the detected shape is. 100% = perfect circle, lower "
    "= elongated or jagged. Keep disabled when annotating off-axis "
    "cameras where real pupils look elliptical."
)


class _ThresholdPupilPanel(QGroupBox):
    """Right-panel widget for the Threshold Pupil plugin."""

    params_changed = pyqtSignal(dict)
    # Emitted when the user toggles the "Pupil ROI" button. The image
    # viewer puts itself in drag-edit mode for the pupil ROI rectangle
    # while this is True.
    roi_edit_requested = pyqtSignal(bool)
    # Emitted when the user clicks Clear next to the ROI button — the
    # canvas drops the rectangle and the plugin re-runs without it.
    clear_roi_requested = pyqtSignal()
    # Emitted when the user toggles the "Show mask" checkbox. The
    # MainWindow forwards this to the image viewer so the threshold
    # mask paint path can be gated independently per plugin.
    show_mask_toggled = pyqtSignal(bool)
    # Emitted when the user flips the "Carry to other images" checkbox
    # next to the ROI row. MainWindow tracks the carry-over state in
    # project settings and applies the value to subsequent image loads
    # that don't already have their own saved ROI.
    carry_roi_toggled = pyqtSignal(bool)
    # Emitted when the user clicks Override — replaces the current
    # image's ROI with whatever the project-wide carry-over holds for
    # the active eye, regardless of any saved ROI this image had.
    override_roi_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the panel widgets and seed them with :data:`DEFAULTS`."""
        super().__init__("Threshold Pupil", parent)
        self._params: dict = dict(DEFAULTS)
        self._build_ui()

    # ----- widget construction -----

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        layout.addLayout(self._build_threshold_row())
        layout.addLayout(self._build_method_row())
        self.fit_row = GateRow(
            "Min ellipse fit",
            initial_enabled=bool(self._params["min_ellipse_fit_enabled"]),
            initial_pct=int(self._params["min_ellipse_fit_pct"]),
            tooltip=ELLIPSE_FIT_TOOLTIP,
        )
        self.fit_row.toggled.connect(lambda on: self._on_gate_toggled("min_ellipse_fit_enabled", on))
        self.fit_row.pct_changed.connect(lambda v: self._on_gate_pct_changed("min_ellipse_fit_pct", v))
        layout.addWidget(self.fit_row)
        self.roundness_row = GateRow(
            "Min roundness",
            initial_enabled=bool(self._params["min_roundness_enabled"]),
            initial_pct=int(self._params["min_roundness_pct"]),
            tooltip=ROUNDNESS_TOOLTIP,
        )
        self.roundness_row.toggled.connect(lambda on: self._on_gate_toggled("min_roundness_enabled", on))
        self.roundness_row.pct_changed.connect(lambda v: self._on_gate_pct_changed("min_roundness_pct", v))
        layout.addWidget(self.roundness_row)
        layout.addLayout(self._build_roi_row())

        self.show_mask_check = QCheckBox("Show mask")
        self.show_mask_check.setChecked(False)
        self.show_mask_check.toggled.connect(self.show_mask_toggled.emit)
        layout.addWidget(self.show_mask_check)

        self.setLayout(layout)

    def _build_threshold_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Threshold:")
        label.setMinimumWidth(100)
        row.addWidget(label)
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(0, 255)
        self.threshold_slider.setValue(self._params["pupil_threshold"])
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 255)
        self.threshold_spin.setValue(self._params["pupil_threshold"])
        self.threshold_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.threshold_spin.setMinimumWidth(50)
        self.threshold_spin.setMaximumWidth(70)
        # Bidirectional sync. setValue is a no-op when the value already
        # matches so this cannot loop; only the slider drives the params
        # handler.
        self.threshold_slider.valueChanged.connect(self.threshold_spin.setValue)
        self.threshold_spin.valueChanged.connect(self.threshold_slider.setValue)
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        row.addWidget(self.threshold_slider)
        row.addWidget(self.threshold_spin)
        return row

    def _build_method_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Center:")
        label.setMinimumWidth(100)
        row.addWidget(label)
        self.method_combo = QComboBox()
        for display, key in CENTER_METHODS:
            self.method_combo.addItem(display, key)
        initial_idx = next(
            (i for i, (_, k) in enumerate(CENTER_METHODS) if k == self._params["pupil_center_method"]),
            0,
        )
        self.method_combo.setCurrentIndex(initial_idx)
        self.method_combo.currentIndexChanged.connect(self._on_method_changed)
        row.addWidget(self.method_combo)
        row.addStretch()
        return row

    def _build_roi_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.roi_button = MaterialButton("Pupil ROI", compact=True)
        self.roi_button.setCheckable(True)
        self.roi_button.toggled.connect(self.roi_edit_requested.emit)
        self.clear_roi_button = MaterialButton("Clear", compact=True)
        self.clear_roi_button.clicked.connect(self.clear_roi_requested.emit)
        self.carry_roi_check = QCheckBox("Carry")
        self.carry_roi_check.setToolTip(
            "When on, this image's ROI is the carry-over source. Loading "
            "another image without its own saved ROI for this eye + target "
            "applies the stored carry-over."
        )
        self.carry_roi_check.toggled.connect(self.carry_roi_toggled.emit)
        self.override_button = MaterialButton("Override", compact=True)
        self.override_button.setToolTip(
            "Replace this image's ROI with the carry-over value, even if "
            "the image already had its own saved ROI for the active eye."
        )
        self.override_button.clicked.connect(self.override_roi_requested.emit)
        row.addWidget(self.roi_button)
        row.addWidget(self.clear_roi_button)
        row.addWidget(self.carry_roi_check)
        row.addWidget(self.override_button)
        return row

    def set_carry_roi_enabled(self, enabled: bool) -> None:
        """Silently set the Carry checkbox state (no ``carry_roi_toggled``)."""
        self.carry_roi_check.blockSignals(True)
        self.carry_roi_check.setChecked(bool(enabled))
        self.carry_roi_check.blockSignals(False)

    def set_override_button_enabled(self, enabled: bool) -> None:
        """Grey / enable the Override button based on whether a carry value is stored."""
        self.override_button.setEnabled(bool(enabled))

    # ----- widget event handlers -----

    def _on_threshold_changed(self, value: int) -> None:
        self._params["pupil_threshold"] = int(value)
        self.params_changed.emit(dict(self._params))

    def _on_method_changed(self, idx: int) -> None:
        key = self.method_combo.itemData(idx)
        self._params["pupil_center_method"] = key
        self.params_changed.emit(dict(self._params))

    def _on_gate_toggled(self, enabled_key: str, checked: bool) -> None:
        self._params[enabled_key] = bool(checked)
        self.params_changed.emit(dict(self._params))

    def _on_gate_pct_changed(self, pct_key: str, value: int) -> None:
        self._params[pct_key] = int(value)
        self.params_changed.emit(dict(self._params))

    # ----- contract surface consumed by the orchestrator -----

    def current_params(self) -> dict:
        """Return a copy of the panel's current parameter dict."""
        return dict(self._params)

    def set_params(self, params: dict) -> None:
        """Populate the widgets from ``params`` without emitting ``params_changed``.

        Signal-blocking covers both the per-widget value-change signals and
        the secondary ``slider <-> spinbox`` mirror, so a single round-trip
        restore stays silent on the wire. The :class:`GateRow` widgets
        have their own :meth:`~GateRow.set_state` for the same purpose.
        """
        widgets = (self.threshold_slider, self.threshold_spin, self.method_combo)
        for w in widgets:
            w.blockSignals(True)
        try:
            if "pupil_threshold" in params:
                value = int(params["pupil_threshold"])
                self.threshold_slider.setValue(value)
                self.threshold_spin.setValue(value)
                self._params["pupil_threshold"] = value
            if "pupil_center_method" in params:
                method = params["pupil_center_method"]
                idx = next((i for i, (_, k) in enumerate(CENTER_METHODS) if k == method), -1)
                if idx >= 0:
                    self.method_combo.setCurrentIndex(idx)
                    self._params["pupil_center_method"] = method
            if "pupil_roi" in params:
                self._params["pupil_roi"] = params["pupil_roi"]
            for row, enabled_key, pct_key in (
                (self.fit_row, "min_ellipse_fit_enabled", "min_ellipse_fit_pct"),
                (self.roundness_row, "min_roundness_enabled", "min_roundness_pct"),
            ):
                if enabled_key in params:
                    self._params[enabled_key] = bool(params[enabled_key])
                if pct_key in params:
                    self._params[pct_key] = int(params[pct_key])
                row.set_state(enabled=self._params[enabled_key], pct=self._params[pct_key])
        finally:
            for w in widgets:
                w.blockSignals(False)

    def set_pupil_roi(self, roi: tuple | None) -> None:
        """Push a canvas-edited ROI into the params dict and re-emit changed.

        Called by the orchestrator when the image viewer finishes a drag
        on the pupil ROI rectangle. Pushes a copy via ``params_changed``
        so the orchestrator's live re-run kicks off.
        """
        self._params["pupil_roi"] = tuple(roi) if roi is not None else None
        self.params_changed.emit(dict(self._params))


class ThresholdPupil(DetectorPlugin):
    """Threshold-based pupil detection backed by ``lavan.detect``."""

    name = "threshold_pupil"
    target = "pupil"
    requires = ()
    live = True
    overlay_z_order = 0
    roi_color = ROI_COLOR
    mask_color = MASK_COLOR

    @classmethod
    def default_params(cls) -> dict:
        """Return a fresh copy of this plugin's default parameter values."""
        return dict(DEFAULTS)

    def make_panel(self, parent: QWidget | None = None) -> QWidget:
        """Build the Qt parameter panel for this plugin."""
        return _ThresholdPupilPanel(parent)

    def detect(
        self,
        image: np.ndarray,
        params: dict,
        shared_results: dict,  # noqa: ARG002 - pupil is a root in the dep graph
    ) -> dict | None:
        """Run the threshold pupil detection on ``image``.

        ``shared_results`` is unused — pupil is a root in the orchestrator
        dependency graph.
        """
        result = detect_pupil(
            image,
            pupil_threshold=int(params["pupil_threshold"]),
            pupil_center_method=params["pupil_center_method"],
            pupil_roi=params.get("pupil_roi"),
            min_ellipse_fit_ratio=(
                int(params["min_ellipse_fit_pct"]) / 100.0 if bool(params.get("min_ellipse_fit_enabled")) else None
            ),
            min_roundness_ratio=(
                int(params["min_roundness_pct"]) / 100.0 if bool(params.get("min_roundness_enabled")) else None
            ),
        )
        if result is None:
            return None
        cx, cy = result["center"]
        (ecx, ecy), (w, h), angle = result["ellipse"]
        return {
            "center": [float(cx), float(cy)],
            "ellipse": {
                "center": [float(ecx), float(ecy)],
                "size": [float(w), float(h)],
                "angle": float(angle),
            },
            # Carried for downstream plugins that consume the pupil
            # contour (glint search region) and for the optional "Show
            # mask" overlay. Both are stripped on serialize().
            "contour": result["contour"],
            "mask": result["mask"],
        }

    def serialize(self, result: dict) -> dict:
        """Reduce a result dict to JSON-friendly types for per-image storage."""
        return {
            "center": list(result["center"]),
            "ellipse": {
                "center": list(result["ellipse"]["center"]),
                "size": list(result["ellipse"]["size"]),
                "angle": float(result["ellipse"]["angle"]),
            },
        }

    def deserialize(self, blob: dict) -> dict:
        """Reconstruct an in-memory result dict from a stored JSON blob."""
        return {
            "center": list(blob["center"]),
            "ellipse": {
                "center": list(blob["ellipse"]["center"]),
                "size": list(blob["ellipse"]["size"]),
                "angle": float(blob["ellipse"]["angle"]),
            },
        }

    def translate_for_crop(self, result: dict, dx: float, dy: float) -> dict:
        """Translate centre + ellipse + contour points from crop coords to full image."""
        translated: dict = {
            "center": [result["center"][0] + dx, result["center"][1] + dy],
            "ellipse": {
                "center": [
                    result["ellipse"]["center"][0] + dx,
                    result["ellipse"]["center"][1] + dy,
                ],
                "size": list(result["ellipse"]["size"]),
                "angle": float(result["ellipse"]["angle"]),
            },
        }
        contour = result.get("contour")
        if contour is not None:
            # Contour is an Nx1x2 or Nx2 ndarray of (x, y) points.
            shifted = contour.copy()
            shifted[..., 0] += round(dx)
            shifted[..., 1] += round(dy)
            translated["contour"] = shifted
        # Mask stays in crop coordinates; MainWindow embeds it into a
        # full-image-sized array before passing it to the viewer.
        if "mask" in result:
            translated["mask"] = result["mask"]
        return translated

    def draw_overlay(self, painter: QPainter, result: dict, scale: float) -> None:
        """Render the fitted pupil ellipse and its centre dot."""
        ellipse = result.get("ellipse")
        if ellipse is not None:
            ecx, ecy = ellipse["center"]
            ew, eh = ellipse["size"]
            angle = float(ellipse["angle"])
            painter.save()
            painter.setPen(QPen(ELLIPSE_COLOR, 1, Qt.SolidLine))
            painter.setBrush(Qt.NoBrush)
            painter.translate(QPointF(ecx * scale, ecy * scale))
            painter.rotate(angle)
            painter.drawEllipse(QPointF(0, 0), (ew / 2) * scale, (eh / 2) * scale)
            painter.restore()
        center = result.get("center")
        if center is not None:
            cx, cy = center
            scaled = QPointF(cx * scale, cy * scale)
            painter.setBrush(CENTER_COLOR)
            painter.setPen(QPen(CENTER_COLOR, 3, Qt.SolidLine))
            painter.drawEllipse(scaled, 1.5, 1.5)
