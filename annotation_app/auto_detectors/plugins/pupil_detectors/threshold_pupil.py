"""Threshold-based pupil detector plugin.

Wraps ``pupil_glint_detector.detect_pupil`` — pupil-only, no implicit
limbus or glint pass. The panel exposes:

  - pupil threshold (slider + linked spinbox),
  - centre-method dropdown (four methods from the underlying algorithm),
  - pupil-ROI toggle + clear (drives canvas drag-edit mode).

The result feeds downstream targets (glint, limbus) via the
orchestrator's ``shared_results`` dict.
"""

import numpy as np
from pupil_glint_detector import detect_pupil
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from annotation_app.auto_detectors.plugin_interface import DetectorPlugin
from annotation_app.gui.custom_widgets import MaterialButton

# Display label / serialised key for each of the four centre-computation
# methods exposed by pupil_glint_detector. Shared shape with the glint
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
}


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
        layout.addLayout(self._build_roi_row())

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
        self.roi_button = MaterialButton("Pupil ROI")
        self.roi_button.setCheckable(True)
        self.roi_button.toggled.connect(self.roi_edit_requested.emit)
        self.clear_roi_button = MaterialButton("Clear")
        self.clear_roi_button.clicked.connect(self.clear_roi_requested.emit)
        row.addWidget(self.roi_button)
        row.addWidget(self.clear_roi_button)
        return row

    # ----- widget event handlers -----

    def _on_threshold_changed(self, value: int) -> None:
        self._params["pupil_threshold"] = int(value)
        self.params_changed.emit(dict(self._params))

    def _on_method_changed(self, idx: int) -> None:
        key = self.method_combo.itemData(idx)
        self._params["pupil_center_method"] = key
        self.params_changed.emit(dict(self._params))

    # ----- contract surface consumed by the orchestrator -----

    def current_params(self) -> dict:
        """Return a copy of the panel's current parameter dict."""
        return dict(self._params)

    def set_params(self, params: dict) -> None:
        """Populate the widgets from ``params`` without emitting ``params_changed``.

        Signal-blocking covers both the per-widget value-change signals and
        the secondary ``slider <-> spinbox`` mirror, so a single round-trip
        restore stays silent on the wire.
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
    """Threshold-based pupil detection backed by ``pupil-glint-detector``."""

    name = "threshold_pupil"
    target = "pupil"
    requires = ()
    live = True

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
            # contour (glint search region) and for the live mask overlay.
            # Stripped from the serialised result.
            "contour": result["contour"],
            "pupil_mask": result["mask"],
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
