"""Daugman integro-differential operator limbus detector plugin.

Backed by ``pupil_glint_detector.detect_limbus``. The Daugman sweep
is too slow for live re-runs on slider drag, so this plugin declares
``live = False``: the orchestrator only invokes ``detect`` when the
user clicks the panel's per-plugin **Detect** button. Changing any
slider clears the previous result so the user is never looking at a
limbus circle that no longer matches the current parameters.

The panel exposes:

  - inner-ring factor (lower bound on iris radius / pupil radius),
  - outer-ring factor (upper bound on iris radius / pupil radius),
  - centre-search window in pixels (±range around the pupil centre),
  - a Detect button that emits ``detect_requested``.
"""

from collections.abc import Callable

import numpy as np
from pupil_glint_detector import detect_limbus
from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
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

# Overlay palette for the Daugman Limbus plugin. Re-uses the muted
# limbus hue so it harmonises with the manual-mode limbus ellipse a
# user might have drawn in a previous session.
LIMBUS_COLOR = QColor(139, 122, 162, 255)

# Slider scale for the two float ring-factor knobs: slider carries an
# int in [1, 100], divided by 10 to recover the actual multiplier in
# (0.1, 10.0].
RING_FACTOR_SCALE = 10

DEFAULTS: dict = {
    "r_min_factor": 1.5,
    "r_max_factor": 5.0,
    "search_window_px": 15,
}


class _DaugmanLimbusPanel(QGroupBox):
    """Right-panel widget for the Daugman Limbus plugin."""

    params_changed = pyqtSignal(dict)
    # Emitted when the user clicks Detect inside this plugin's panel.
    # MainWindow runs the plugin once on the current image; the result
    # populates the cache and the limbus overlay.
    detect_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the panel widgets and seed them with :data:`DEFAULTS`."""
        super().__init__("Daugman Limbus", parent)
        self._params: dict = dict(DEFAULTS)
        self._build_ui()

    # ----- widget construction -----

    def _build_ui(self) -> None:
        layout = QVBoxLayout()
        layout.addLayout(self._build_inner_row())
        layout.addLayout(self._build_outer_row())
        layout.addLayout(self._build_window_row())
        self.detect_button = MaterialButton("Detect")
        self.detect_button.clicked.connect(self.detect_requested.emit)
        layout.addWidget(self.detect_button)
        self.setLayout(layout)

    def _build_inner_row(self) -> QHBoxLayout:
        return self._build_factor_row(
            label_text="Inner radius:",
            slider_attr="inner_slider",
            spin_attr="inner_spin",
            initial=float(self._params["r_min_factor"]),
            slider_range=(1, 5 * RING_FACTOR_SCALE),
            spin_range=(0.1, 5.0),
            on_change=self._on_inner_changed,
            tooltip=(
                "Lower bound on the iris radius search, expressed as a multiple of "
                "the pupil radius. 1.5 = the iris radius is at least 1.5 x the pupil's."
            ),
        )

    def _build_outer_row(self) -> QHBoxLayout:
        return self._build_factor_row(
            label_text="Outer radius:",
            slider_attr="outer_slider",
            spin_attr="outer_spin",
            initial=float(self._params["r_max_factor"]),
            slider_range=(2 * RING_FACTOR_SCALE, 10 * RING_FACTOR_SCALE),
            spin_range=(0.5, 10.0),
            on_change=self._on_outer_changed,
            tooltip=(
                "Upper bound on the iris radius search, expressed as a multiple of "
                "the pupil radius. 5.0 = the iris radius is at most 5.0 x the pupil's."
            ),
        )

    def _build_factor_row(
        self,
        *,
        label_text: str,
        slider_attr: str,
        spin_attr: str,
        initial: float,
        slider_range: tuple[int, int],
        spin_range: tuple[float, float],
        on_change: Callable[..., None],
        tooltip: str,
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel(label_text)
        label.setMinimumWidth(110)
        label.setToolTip(tooltip)
        row.addWidget(label)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(*slider_range)
        slider.setValue(round(initial * RING_FACTOR_SCALE))
        spin = QDoubleSpinBox()
        spin.setRange(*spin_range)
        spin.setSingleStep(0.1)
        spin.setDecimals(1)
        spin.setSuffix(" x pupil_r")
        spin.setValue(initial)
        spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        spin.setMinimumWidth(80)
        spin.setMaximumWidth(110)
        slider.valueChanged.connect(lambda v: on_change(v / RING_FACTOR_SCALE, source="slider"))
        spin.valueChanged.connect(lambda v: on_change(v, source="spin"))
        setattr(self, slider_attr, slider)
        setattr(self, spin_attr, spin)
        row.addWidget(slider)
        row.addWidget(spin)
        return row

    def _build_window_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel("Search window (px):")
        label.setMinimumWidth(110)
        label.setToolTip(
            "Half-width of the centre-search box around the pupil centre. The "
            "Daugman operator sweeps every offset within this many pixels of "
            "the pupil centre, so larger values are slower but more tolerant.",
        )
        row.addWidget(label)
        self.window_spin = QSpinBox()
        self.window_spin.setRange(1, 100)
        self.window_spin.setValue(int(self._params["search_window_px"]))
        self.window_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.window_spin.setMinimumWidth(60)
        self.window_spin.setMaximumWidth(90)
        self.window_spin.valueChanged.connect(self._on_window_changed)
        row.addWidget(self.window_spin)
        row.addStretch()
        return row

    # ----- widget event handlers -----

    def _on_inner_changed(self, value: float, *, source: str) -> None:
        self._sync_factor_pair(value, source=source, slider_attr="inner_slider", spin_attr="inner_spin")
        self._params["r_min_factor"] = float(value)
        self.params_changed.emit(dict(self._params))

    def _on_outer_changed(self, value: float, *, source: str) -> None:
        self._sync_factor_pair(value, source=source, slider_attr="outer_slider", spin_attr="outer_spin")
        self._params["r_max_factor"] = float(value)
        self.params_changed.emit(dict(self._params))

    def _sync_factor_pair(self, value: float, *, source: str, slider_attr: str, spin_attr: str) -> None:
        # The slider drives a float spinbox and vice versa. Block signals
        # on the partner widget so the mirror does not bounce back and
        # double-emit params_changed.
        slider = getattr(self, slider_attr)
        spin = getattr(self, spin_attr)
        if source == "slider":
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        else:
            slider.blockSignals(True)
            slider.setValue(round(value * RING_FACTOR_SCALE))
            slider.blockSignals(False)

    def _on_window_changed(self, value: int) -> None:
        self._params["search_window_px"] = int(value)
        self.params_changed.emit(dict(self._params))

    # ----- contract surface consumed by the orchestrator -----

    def current_params(self) -> dict:
        """Return a copy of the panel's current parameter dict."""
        return dict(self._params)

    def set_params(self, params: dict) -> None:
        """Populate the widgets from ``params`` without emitting ``params_changed``."""
        widgets = (
            self.inner_slider,
            self.inner_spin,
            self.outer_slider,
            self.outer_spin,
            self.window_spin,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            if "r_min_factor" in params:
                v = float(params["r_min_factor"])
                self.inner_slider.setValue(round(v * RING_FACTOR_SCALE))
                self.inner_spin.setValue(v)
                self._params["r_min_factor"] = v
            if "r_max_factor" in params:
                v = float(params["r_max_factor"])
                self.outer_slider.setValue(round(v * RING_FACTOR_SCALE))
                self.outer_spin.setValue(v)
                self._params["r_max_factor"] = v
            if "search_window_px" in params:
                v = int(params["search_window_px"])
                self.window_spin.setValue(v)
                self._params["search_window_px"] = v
        finally:
            for w in widgets:
                w.blockSignals(False)


class DaugmanLimbus(DetectorPlugin):
    """Daugman IDO limbus detection. Lazy — only runs via the panel's Detect button."""

    name = "daugman_limbus"
    target = "limbus"
    requires = ("pupil",)
    live = False
    # Drawn behind pupil + glint markers so the iris ring frames them
    # without obscuring the bright details.
    overlay_z_order = -10

    @classmethod
    def default_params(cls) -> dict:
        """Return a fresh copy of this plugin's default parameter values."""
        return dict(DEFAULTS)

    def make_panel(self, parent: QWidget | None = None) -> QWidget:
        """Build the Qt parameter panel for this plugin."""
        return _DaugmanLimbusPanel(parent)

    def detect(
        self,
        image: np.ndarray,
        params: dict,
        shared_results: dict,
    ) -> dict | None:
        """Run the Daugman IDO limbus detection on ``image``."""
        pupil = shared_results["pupil"]
        pupil_center = tuple(pupil["center"])
        ew, eh = pupil["ellipse"]["size"]
        pupil_radius = max(ew, eh) / 2.0
        result = detect_limbus(
            image,
            pupil_center=pupil_center,
            pupil_radius=pupil_radius,
            r_min_factor=float(params["r_min_factor"]),
            r_max_factor=float(params["r_max_factor"]),
            search_window_px=int(params["search_window_px"]),
        )
        if result is None:
            return None
        lcx, lcy = result["center"]
        return {
            "center": [float(lcx), float(lcy)],
            "radius": float(result["radius"]),
        }

    def serialize(self, result: dict) -> dict:
        """Reduce a result dict to JSON-friendly types for per-image storage."""
        return {
            "center": list(result["center"]),
            "radius": float(result["radius"]),
        }

    def deserialize(self, blob: dict) -> dict:
        """Reconstruct an in-memory result dict from a stored JSON blob."""
        return {
            "center": list(blob["center"]),
            "radius": float(blob["radius"]),
        }

    def draw_overlay(self, painter: QPainter, result: dict, scale: float) -> None:
        """Render the detected limbus as a single circle outline."""
        center = result.get("center")
        radius = result.get("radius")
        if center is None or radius is None:
            return
        cx, cy = center
        painter.save()
        painter.setPen(QPen(LIMBUS_COLOR, 1, Qt.SolidLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(
            QPointF(cx * scale, cy * scale),
            float(radius) * scale,
            float(radius) * scale,
        )
        painter.restore()
