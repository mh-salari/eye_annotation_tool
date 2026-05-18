"""Pupil Labs 2D detector plugin.

Wraps :class:`lavan.pupil_detector_2d.Detector2D`, which is the
Pupil Core 2D pupil detector (LGPL v3, vendored into lavan from
pupil-labs/pupil-detectors).

The panel exposes the practical knobs from ``Detector2D.get_properties()``:

  - intensity range (slider + linked spinbox) — pupil-vs-iris brightness gap,
  - pupil-size min / max (linked spinboxes) — diameter bounds in pixels,
  - blur size (spinbox) — odd kernel for the pre-detection gaussian,
  - coarse-detection toggle,
  - min confidence (double spinbox) — results below this score are dropped,
  - pupil-ROI toggle + clear + carry + override (drives canvas drag-edit mode).

The remaining 16 advanced ``Detector2D`` knobs (canny ratios, perimeter /
area gates, etc.) stay at their defaults; advanced users can edit them
via the saved project's per-eye params dict.
"""

import numpy as np
from lavan.pupil_detector_2d import Detector2D, Roi
from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
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

# Distinct palette from threshold_pupil — slightly bluer green so the two
# pupil plugins are visually distinguishable when a user switches between
# them and compares overlays. Matches the threshold plugin's saturation /
# luminance so the canvas keeps a consistent feel.
ELLIPSE_COLOR = QColor(35, 175, 130, 255)
CENTER_COLOR = QColor(120, 240, 180, 255)
ROI_COLOR = CENTER_COLOR

DEFAULTS: dict = {
    "intensity_range": 23,
    "pupil_size_min": 10,
    "pupil_size_max": 100,
    "blur_size": 5,
    "coarse_detection": True,
    "min_confidence": 0.5,
    "pupil_roi": None,
}


class _PupilLabs2DPanel(QGroupBox):
    """Right-panel widget for the Pupil Labs 2D detector plugin."""

    params_changed = pyqtSignal(dict)
    roi_edit_requested = pyqtSignal(bool)
    clear_roi_requested = pyqtSignal()
    show_ellipse_toggled = pyqtSignal(bool)
    carry_roi_toggled = pyqtSignal(bool)
    override_roi_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the panel widgets and seed them with :data:`DEFAULTS`."""
        super().__init__("Pupil Labs 2D", parent)
        self._params: dict = dict(DEFAULTS)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        layout.addLayout(self._build_intensity_row())
        layout.addLayout(self._build_size_row("Pupil min (px)", "pupil_size_min", 1, 500))
        layout.addLayout(self._build_size_row("Pupil max (px)", "pupil_size_max", 1, 500))
        layout.addLayout(self._build_blur_row())
        layout.addLayout(self._build_confidence_row())
        layout.addLayout(self._build_flags_row())
        layout.addLayout(self._build_roi_row())

        toggles_row = QHBoxLayout()
        self.show_ellipse_check = QCheckBox("Show ellipse")
        self.show_ellipse_check.setChecked(True)
        self.show_ellipse_check.toggled.connect(self.show_ellipse_toggled.emit)
        toggles_row.addWidget(self.show_ellipse_check)
        toggles_row.addStretch()
        layout.addLayout(toggles_row)

        self.setLayout(layout)

    def _build_intensity_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Intensity range:")
        label.setMinimumWidth(110)
        label.setToolTip(
            "Pupil-vs-iris brightness gap, in 0-255 grey levels. Larger "
            "values let the algorithm accept dimmer pupil contours; "
            "smaller values restrict it to high-contrast pupils.",
        )
        row.addWidget(label)
        self.intensity_slider = QSlider(Qt.Horizontal)
        self.intensity_slider.setRange(0, 255)
        self.intensity_slider.setValue(self._params["intensity_range"])
        self.intensity_spin = QSpinBox()
        self.intensity_spin.setRange(0, 255)
        self.intensity_spin.setValue(self._params["intensity_range"])
        self.intensity_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.intensity_spin.setMinimumWidth(50)
        self.intensity_spin.setMaximumWidth(70)
        # Bidirectional sync; only the slider drives params updates.
        self.intensity_slider.valueChanged.connect(self.intensity_spin.setValue)
        self.intensity_spin.valueChanged.connect(self.intensity_slider.setValue)
        self.intensity_slider.valueChanged.connect(self._on_intensity_changed)
        row.addWidget(self.intensity_slider)
        row.addWidget(self.intensity_spin)
        return row

    def _build_size_row(self, label_text: str, key: str, lo: int, hi: int) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel(label_text + ":")
        label.setMinimumWidth(110)
        row.addWidget(label)
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(int(self._params[key]))
        spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        spin.setMinimumWidth(60)
        spin.setMaximumWidth(90)
        spin.valueChanged.connect(lambda v, k=key: self._on_int_changed(k, v))
        row.addWidget(spin)
        row.addStretch()
        # Stash the widget so set_params can drive it later.
        setattr(self, f"{key}_spin", spin)
        return row

    def _build_blur_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Blur size:")
        label.setMinimumWidth(110)
        label.setToolTip(
            "Pre-detection Gaussian blur kernel. Must be odd; the plugin "
            "rounds even values up by one before passing to Detector2D.",
        )
        row.addWidget(label)
        self.blur_spin = QSpinBox()
        self.blur_spin.setRange(1, 21)
        self.blur_spin.setSingleStep(2)
        self.blur_spin.setValue(int(self._params["blur_size"]))
        self.blur_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.blur_spin.setMinimumWidth(60)
        self.blur_spin.setMaximumWidth(90)
        self.blur_spin.valueChanged.connect(lambda v: self._on_int_changed("blur_size", v))
        row.addWidget(self.blur_spin)
        row.addStretch()
        return row

    def _build_confidence_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Min confidence:")
        label.setMinimumWidth(110)
        label.setToolTip(
            "Detector2D returns a confidence in [0, 1]. Results below "
            "this threshold are dropped (no overlay), so a poorly-fit "
            "ellipse never reaches the rest of the pipeline.",
        )
        row.addWidget(label)
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.0, 1.0)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setDecimals(2)
        self.confidence_spin.setValue(float(self._params["min_confidence"]))
        self.confidence_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.confidence_spin.setMinimumWidth(70)
        self.confidence_spin.setMaximumWidth(90)
        self.confidence_spin.valueChanged.connect(self._on_confidence_changed)
        row.addWidget(self.confidence_spin)
        row.addStretch()
        return row

    def _build_flags_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel(" ")
        label.setMinimumWidth(110)
        row.addWidget(label)
        self.coarse_check = QCheckBox("Coarse detection")
        self.coarse_check.setToolTip(
            "Enables Pupil Core's coarse-pupil pre-pass that narrows the "
            "search region before the fine fit. Disable on very small "
            "pupils where the coarse pass misclassifies the iris.",
        )
        self.coarse_check.setChecked(bool(self._params["coarse_detection"]))
        self.coarse_check.toggled.connect(lambda on: self._on_bool_changed("coarse_detection", on))
        row.addWidget(self.coarse_check)
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
            "When on, this image's ROI is the carry-over source for "
            "subsequent images that have no saved ROI for this eye.",
        )
        self.carry_roi_check.toggled.connect(self.carry_roi_toggled.emit)
        self.override_button = MaterialButton("Override", compact=True)
        self.override_button.setToolTip(
            "Replace this image's ROI with the carry-over value, even if "
            "the image already had its own saved ROI for the active eye.",
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

    def _on_intensity_changed(self, value: int) -> None:
        self._params["intensity_range"] = int(value)
        self.params_changed.emit(dict(self._params))

    def _on_int_changed(self, key: str, value: int) -> None:
        self._params[key] = int(value)
        self.params_changed.emit(dict(self._params))

    def _on_bool_changed(self, key: str, value: bool) -> None:
        self._params[key] = bool(value)
        self.params_changed.emit(dict(self._params))

    def _on_confidence_changed(self, value: float) -> None:
        self._params["min_confidence"] = float(value)
        self.params_changed.emit(dict(self._params))

    # ----- contract surface consumed by the orchestrator -----

    def current_params(self) -> dict:
        """Return a copy of the panel's current parameter dict."""
        return dict(self._params)

    def set_params(self, params: dict) -> None:
        """Populate the widgets from ``params`` without emitting ``params_changed``."""
        widgets = (
            self.intensity_slider,
            self.intensity_spin,
            self.pupil_size_min_spin,
            self.pupil_size_max_spin,
            self.blur_spin,
            self.confidence_spin,
            self.coarse_check,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            if "intensity_range" in params:
                value = int(params["intensity_range"])
                self.intensity_slider.setValue(value)
                self.intensity_spin.setValue(value)
                self._params["intensity_range"] = value
            for int_key, spin in (
                ("pupil_size_min", self.pupil_size_min_spin),
                ("pupil_size_max", self.pupil_size_max_spin),
                ("blur_size", self.blur_spin),
            ):
                if int_key in params:
                    spin.setValue(int(params[int_key]))
                    self._params[int_key] = int(params[int_key])
            if "min_confidence" in params:
                self.confidence_spin.setValue(float(params["min_confidence"]))
                self._params["min_confidence"] = float(params["min_confidence"])
            if "coarse_detection" in params:
                self.coarse_check.setChecked(bool(params["coarse_detection"]))
                self._params["coarse_detection"] = bool(params["coarse_detection"])
            if "pupil_roi" in params:
                self._params["pupil_roi"] = params["pupil_roi"]
        finally:
            for w in widgets:
                w.blockSignals(False)

    def set_pupil_roi(self, roi: tuple | None) -> None:
        """Push a canvas-edited ROI into the params dict and re-emit ``params_changed``."""
        self._params["pupil_roi"] = tuple(roi) if roi is not None else None
        self.params_changed.emit(dict(self._params))


def _next_odd(value: int) -> int:
    """Round even values up by one so blur_size stays odd."""
    return value if value % 2 == 1 else value + 1


class PupilLabs2D(DetectorPlugin):
    """Pupil Core 2D pupil detector backed by ``lavan.pupil_detector_2d.Detector2D``."""

    name = "pupil_labs_2d"
    target = "pupil"
    requires = ()
    live = True
    overlay_z_order = 0
    roi_color = ROI_COLOR
    _show_ellipse: bool = True

    def __init__(self) -> None:
        """Construct the plugin + its persistent C++ Detector2D instance."""
        super().__init__()
        # One detector per plugin lifetime; ``update_properties`` mutates
        # it in place between detect() calls so we don't pay the C++
        # constructor cost on every slider tick.
        self._detector = Detector2D()

    @classmethod
    def default_params(cls) -> dict:
        """Return a fresh copy of this plugin's default parameter values."""
        return dict(DEFAULTS)

    def make_panel(self, parent: QWidget | None = None) -> QWidget:
        """Build the Qt parameter panel for this plugin."""
        return _PupilLabs2DPanel(parent)

    def set_show_ellipse(self, on: bool) -> None:
        """Set whether :meth:`draw_overlay` renders the fitted ellipse outline."""
        self._show_ellipse = bool(on)

    def detect(
        self,
        image: np.ndarray,
        params: dict,
        shared_results: dict,  # noqa: ARG002 - pupil is a root in the dep graph
    ) -> dict | None:
        """Run the Pupil Labs 2D detector on ``image``."""
        self._detector.update_properties({
            "intensity_range": int(params["intensity_range"]),
            "pupil_size_min": int(params["pupil_size_min"]),
            "pupil_size_max": int(params["pupil_size_max"]),
            "blur_size": _next_odd(int(params["blur_size"])),
            "coarse_detection": bool(params["coarse_detection"]),
        })
        roi_rect = params.get("pupil_roi")
        roi = None
        if roi_rect is not None:
            x, y, w, h = (int(v) for v in roi_rect)
            roi = Roi(x, y, x + w, y + h)
        # Detector2D's Cython binding takes a typed memoryview over the
        # input ndarray, which requires C-contiguous storage; the
        # binocular crop the orchestrator hands us is a slice (a view)
        # so we materialize a contiguous copy on the way in.
        if not image.flags["C_CONTIGUOUS"]:
            image = np.ascontiguousarray(image)
        result = self._detector.detect(image, roi=roi)
        if not result or float(result.get("confidence", 0.0)) < float(params["min_confidence"]):
            return None
        cx, cy = result["location"]
        ellipse = result["ellipse"]
        ecx, ecy = ellipse["center"]
        ew, eh = ellipse["axes"]
        return {
            "center": [float(cx), float(cy)],
            "ellipse": {
                "center": [float(ecx), float(ecy)],
                "size": [float(ew), float(eh)],
                "angle": float(ellipse["angle"]),
            },
            "confidence": float(result["confidence"]),
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
            "confidence": float(result.get("confidence", 0.0)),
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
            "confidence": float(blob.get("confidence", 0.0)),
        }

    def translate_for_crop(self, result: dict, dx: float, dy: float) -> dict:
        """Shift centre + ellipse centre from crop coords to full image."""
        return {
            "center": [result["center"][0] + dx, result["center"][1] + dy],
            "ellipse": {
                "center": [
                    result["ellipse"]["center"][0] + dx,
                    result["ellipse"]["center"][1] + dy,
                ],
                "size": list(result["ellipse"]["size"]),
                "angle": float(result["ellipse"]["angle"]),
            },
            "confidence": float(result.get("confidence", 0.0)),
        }

    def draw_overlay(self, painter: QPainter, result: dict, scale: float) -> None:
        """Render the fitted pupil ellipse outline and its centre dot."""
        ellipse = result.get("ellipse")
        if ellipse is not None and self._show_ellipse:
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
