"""Per-kind detector card.

One card per anatomical kind (pupil / glint / limbus / eyelid). Each
card holds the detector picker (Off | Manual | detector id…), the
overlay row, Reset / Save-default buttons, the active detector's
settings widgets, and (when the detector exposes an ROI setting) the
ROI affordance row. When the user picks Manual the card hosts the
kind's manual annotation group widget instead.
"""

from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from eye_annotation_tool.auto_detectors.plugin import DetectorPlugin as Detector

from .custom_widgets import MaterialButton
from .detector_setting_widgets import SettingsBlock

# A detector setting value, spanning every ``SettingSpec.type`` tag.
SettingValue = int | float | str | bool | tuple | None

_TYPE_DEFAULT_ALPHA = {"line": 1.0, "point": 1.0, "fill": 0.5}
_TYPE_DEFAULT_THICKNESS = {"line": 1, "point": 1, "fill": 0}
_TYPE_DEFAULT_SHOW = {"line": True, "point": True, "fill": False}

# Per-overlay default colour, keyed by f"{kind}_{overlay_key}". Stored
# in BGR so the values match the colour names used by the underlying
# detector library; converted to RGB on the way into Qt.
_DEFAULT_BGR_BY_FULL_KEY: dict[str, tuple[int, int, int]] = {
    "pupil_contour": (0, 255, 0),
    "pupil_ellipse": (0, 255, 255),
    "pupil_center": (0, 255, 0),
    "pupil_mask": (0, 200, 0),
    "glint_contour": (0, 0, 255),
    "glint_center": (0, 0, 255),
    "glint_mask": (0, 60, 200),
    "limbus_curve": (255, 0, 255),
    "limbus_center": (255, 0, 255),
    "limbus_mask": (255, 0, 255),
}

OFF = "off"
MANUAL = "manual"

# Overlay keys the canvas paints for the manual annotation path, per kind.
# The card exposes the same row of controls (visibility / colour / alpha /
# thickness or point size) the cheshm-style auto detectors get, so the
# canvas reads colour + size for points and ellipses from one place
# regardless of which mode owns the kind.
MANUAL_OVERLAYS_BY_KIND: dict[str, tuple[tuple[str, str], ...]] = {
    "pupil": (("points", "point"), ("ellipse", "line"), ("center", "point")),
    "limbus": (("points", "point"), ("ellipse", "line"), ("center", "point")),
    "eyelid": (("points", "point"),),
    "glint": (("points", "point"),),
}

# RGB defaults for the manual overlay rows. Match the original
# AnnotationColors palette so the canvas looks identical out of the
# box; the user can re-tint via the card.
_DEFAULT_MANUAL_COLOR_BY_KIND_KEY: dict[tuple[str, str], tuple[int, int, int]] = {
    ("pupil", "points"): (150, 213, 116),
    ("pupil", "ellipse"): (25, 145, 50),
    ("pupil", "center"): (180, 240, 80),
    ("limbus", "points"): (194, 149, 188),
    ("limbus", "ellipse"): (139, 122, 162),
    ("limbus", "center"): (139, 122, 162),
    ("eyelid", "points"): (0, 155, 201),
    ("glint", "points"): (255, 165, 0),
}


def _bgr_to_qcolor(bgr: tuple[int, int, int]) -> QColor:
    b, g, r = bgr
    return QColor(r, g, b)


def default_overlay_state(kind: str, overlays: tuple[tuple[str, str], ...]) -> dict[str, dict[str, Any]]:
    """Return a fresh per-overlay-key state dict for one detector's overlays."""
    state: dict[str, dict[str, Any]] = {}
    for key, elem_type in overlays:
        full = f"{kind}_{key}"
        state[key] = {
            "show": _TYPE_DEFAULT_SHOW.get(elem_type, True),
            "color": _bgr_to_qcolor(_DEFAULT_BGR_BY_FULL_KEY.get(full, (255, 255, 255))),
            "alpha": _TYPE_DEFAULT_ALPHA.get(elem_type, 1.0),
            "thickness": _TYPE_DEFAULT_THICKNESS.get(elem_type, 1),
            "type": elem_type,
        }
    return state


def default_manual_overlay_state(kind: str) -> dict[str, dict[str, Any]]:
    """Return a fresh manual-annotation overlay state dict for ``kind``."""
    state: dict[str, dict[str, Any]] = {}
    for key, elem_type in MANUAL_OVERLAYS_BY_KIND.get(kind, ()):
        rgb = _DEFAULT_MANUAL_COLOR_BY_KIND_KEY.get((kind, key), (255, 255, 255))
        state[key] = {
            "show": True,
            "color": QColor(*rgb),
            "alpha": 1.0,
            "thickness": 2 if elem_type == "point" else 1,
            "type": elem_type,
        }
    return state


def detector_has_roi(detector: Detector | None) -> str | None:
    """Return the name of the detector's ROI setting, or ``None`` if it has none."""
    if detector is None:
        return None
    for s in detector.settings:
        if s.type == "roi":
            return s.name
    return None


# ---------------------------------------------------------------------------
# Overlay row: per-key show / colour / alpha / thickness controls
# ---------------------------------------------------------------------------


class _ColorSwatch(QPushButton):
    """Small clickable colour rectangle; opens QColorDialog on click."""

    color_changed = pyqtSignal(QColor)

    def __init__(self, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._color = QColor(color)
        self._refresh()
        self.clicked.connect(self._pick)

    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self._refresh()

    def _refresh(self) -> None:
        palette = self.palette()
        palette.setColor(QPalette.Button, self._color)
        self.setPalette(palette)
        self.setStyleSheet(f"background-color: rgb({self._color.red()},{self._color.green()},{self._color.blue()});")

    def _pick(self) -> None:
        new = QColorDialog.getColor(self._color, self, "Pick overlay colour")
        if not new.isValid():
            return
        self._color = new
        self._refresh()
        self.color_changed.emit(self._color)


class OverlayRow(QWidget):
    """Collapsing block of per-overlay-key rows for the active detector."""

    overlay_changed = pyqtSignal(str, str, object)  # (overlay_key, field, new_value)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        self._toggle = QToolButton()
        self._toggle.setText("overlay")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(False)
        self._toggle.setArrowType(Qt.RightArrow)
        self._toggle.toggled.connect(self._on_toggle)
        outer.addWidget(self._toggle)
        self._body = QFrame()
        self._body_layout = QVBoxLayout()
        self._body_layout.setContentsMargins(8, 0, 0, 0)
        self._body.setLayout(self._body_layout)
        self._body.setVisible(False)
        outer.addWidget(self._body)
        self.setLayout(outer)

    def _on_toggle(self, expanded: bool) -> None:
        self._toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._body.setVisible(expanded)

    def populate(self, state: dict[str, dict[str, Any]]) -> None:
        """Rebuild the row from a fresh overlay-state dict."""
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for key, entry in state.items():
            self._body_layout.addLayout(self._build_row(key, entry))

    def _build_row(self, key: str, entry: dict[str, Any]) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 2)
        show = QCheckBox()
        show.setChecked(bool(entry["show"]))
        show.toggled.connect(lambda v, k=key: self.overlay_changed.emit(k, "show", bool(v)))
        row.addWidget(show)
        row.addWidget(QLabel(key))
        swatch = _ColorSwatch(entry["color"])
        swatch.color_changed.connect(lambda c, k=key: self.overlay_changed.emit(k, "color", QColor(c)))
        row.addWidget(swatch)
        alpha = QSlider(Qt.Horizontal)
        alpha.setRange(0, 100)
        alpha.setValue(int(float(entry["alpha"]) * 100))
        alpha.setFixedWidth(70)
        alpha.valueChanged.connect(lambda v, k=key: self.overlay_changed.emit(k, "alpha", float(v) / 100.0))
        row.addWidget(alpha)
        elem_type = entry.get("type", "line")
        if elem_type in {"line", "point"}:
            thickness = QSpinBox()
            thickness.setRange(1, 20)
            thickness.setValue(int(entry["thickness"]))
            thickness.setMaximumWidth(50)
            thickness.setButtonSymbols(QAbstractSpinBox.NoButtons)
            thickness.valueChanged.connect(lambda v, k=key: self.overlay_changed.emit(k, "thickness", int(v)))
            row.addWidget(thickness)
        row.addStretch(1)
        return row


# ---------------------------------------------------------------------------
# ROI affordance row (visible only when the active detector has an ROI)
# ---------------------------------------------------------------------------


class _RoiRow(QWidget):
    """ROI button + Clear + Carry checkbox + Override button."""

    roi_edit_requested = pyqtSignal(bool)
    clear_roi_requested = pyqtSignal()
    carry_roi_toggled = pyqtSignal(bool)
    override_roi_requested = pyqtSignal()

    def __init__(self, kind_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.roi_button = MaterialButton(f"{kind_label} ROI", compact=True)
        self.roi_button.setCheckable(True)
        self.roi_button.toggled.connect(self.roi_edit_requested.emit)
        self.clear_button = MaterialButton("Clear", compact=True)
        self.clear_button.clicked.connect(self.clear_roi_requested.emit)
        self.carry_check = QCheckBox("Carry")
        self.carry_check.setToolTip(
            "When on, this image's ROI is the carry-over source for other "
            "images that have no saved ROI yet for this eye + kind.",
        )
        self.carry_check.toggled.connect(self.carry_roi_toggled.emit)
        self.override_button = MaterialButton("Override", compact=True)
        self.override_button.setToolTip(
            "Replace this image's ROI with the stored carry-over rectangle, "
            "even if the image already had its own saved ROI for the active eye.",
        )
        self.override_button.clicked.connect(self.override_roi_requested.emit)
        row.addWidget(self.roi_button)
        row.addWidget(self.clear_button)
        row.addWidget(self.carry_check)
        row.addWidget(self.override_button)
        self.setLayout(row)

    def set_carry_enabled(self, enabled: bool) -> None:
        self.carry_check.blockSignals(True)
        self.carry_check.setChecked(bool(enabled))
        self.carry_check.blockSignals(False)

    def set_override_enabled(self, enabled: bool) -> None:
        self.override_button.setEnabled(bool(enabled))

    def set_roi_button_checked(self, checked: bool) -> None:
        self.roi_button.blockSignals(True)
        self.roi_button.setChecked(bool(checked))
        self.roi_button.blockSignals(False)


# ---------------------------------------------------------------------------
# DetectorCard: one per kind
# ---------------------------------------------------------------------------


class DetectorCard(QFrame):
    """Per-kind detector card — single source for dropdown / overlay / settings."""

    # Payload: new selection slug. ``"off"`` / ``"manual"`` / a cheshm detector id.
    selection_changed = pyqtSignal(str)
    # Payload: dict of current setting values for the active detector.
    params_changed = pyqtSignal(dict)
    # Overlay state change: (overlay_key, field_name, new_value).
    overlay_changed = pyqtSignal(str, str, object)
    reset_requested = pyqtSignal()
    save_default_requested = pyqtSignal()
    # User clicked the per-card "Detect" button — runs the active auto
    # detector on the current image without needing a settings change.
    detect_requested = pyqtSignal()

    # Re-emitted from the ROI affordance row.
    roi_edit_requested = pyqtSignal(bool)
    clear_roi_requested = pyqtSignal()
    carry_roi_toggled = pyqtSignal(bool)
    override_roi_requested = pyqtSignal()

    def __init__(
        self,
        kind: str,
        detectors_for_kind: list[Detector],
        parent: QWidget | None = None,
    ) -> None:
        """Build the card UI for ``kind`` from its available detectors."""
        super().__init__(parent)
        self.kind = kind
        self._detectors = list(detectors_for_kind)
        self._detector_by_id: dict[str, Detector] = {d.name: d for d in self._detectors}
        self._active_id: str = OFF
        self._values: dict[str, dict[str, Any]] = {
            d.name: {s.name: s.default for s in d.settings} for d in self._detectors
        }
        self._overlay_state: dict[str, dict[str, dict[str, Any]]] = {
            d.name: default_overlay_state(kind, d.overlays) for d in self._detectors
        }
        # Per-kind manual-annotation overlay state. Read by the canvas
        # renderer when the kind is in Manual mode so the user can pick
        # the colour, alpha, and point / line size of the manual marks.
        self._manual_overlay_state: dict[str, dict[str, Any]] = default_manual_overlay_state(kind)
        self._manual_host: QWidget | None = None
        self._settings_block: SettingsBlock | None = None
        self._roi_row: _RoiRow | None = None

        self.setFrameShape(QFrame.StyledPanel)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel(kind.upper())
        layout.addWidget(title)

        picker_row = QHBoxLayout()
        picker_row.addWidget(QLabel("detector"))
        self._combo = QComboBox()
        self._combo.addItem("Off", OFF)
        self._combo.addItem("Manual", MANUAL)
        for det in self._detectors:
            self._combo.addItem(det.name, det.name)
        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        picker_row.addWidget(self._combo, 1)
        # Per-card Detect button — re-runs the active auto detector
        # without needing a slider tweak. Hidden in Off and Manual.
        self._detect_button = MaterialButton("Detect", compact=True)
        self._detect_button.clicked.connect(self.detect_requested.emit)
        picker_row.addWidget(self._detect_button)
        layout.addLayout(picker_row)

        self._overlay_row = OverlayRow()
        self._overlay_row.overlay_changed.connect(self._on_overlay_changed)
        layout.addWidget(self._overlay_row)

        button_row = QHBoxLayout()
        self._reset_button = MaterialButton("Reset to defaults", compact=True)
        self._reset_button.clicked.connect(self._on_reset_clicked)
        self._save_default_button = MaterialButton("Save as default", compact=True)
        self._save_default_button.clicked.connect(self._on_save_default_clicked)
        button_row.addWidget(self._reset_button)
        button_row.addWidget(self._save_default_button)
        layout.addLayout(button_row)

        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._content_layout)

        self.setLayout(layout)
        self._refresh_content()

    # ----- public API -----

    def detectors(self) -> list[Detector]:
        """Return the detectors available for this card's kind."""
        return list(self._detectors)

    def active_id(self) -> str:
        """Return the active selection slug (Off / Manual / detector id)."""
        return self._active_id

    def active_detector(self) -> Detector | None:
        """Return the active detector, or ``None`` when off/manual."""
        return self._detector_by_id.get(self._active_id)

    def current_params(self) -> dict[str, Any]:
        """Return the active detector's current setting values."""
        if self._settings_block is None:
            return {}
        return self._settings_block.current_values()

    def overlay_state(self) -> dict[str, dict[str, Any]] | None:
        """Return the overlay state for the current selection.

        Off returns ``None``. Manual returns the per-kind manual overlay
        state (keys vary by kind — pupil/limbus carry ``points`` /
        ``ellipse`` / ``center``; eyelid and glint carry only ``points``).
        Any auto detector returns its cheshm overlay state.
        """
        if self._active_id == MANUAL:
            return self._manual_overlay_state
        det = self.active_detector()
        if det is None:
            return None
        return self._overlay_state[det.name]

    def set_selection(self, selection: str, *, emit: bool = False) -> None:
        """Select Off / Manual / detector_id without firing ``selection_changed`` unless asked."""
        index = self._combo.findData(selection)
        index = max(index, 0)
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(index)
        self._combo.blockSignals(False)
        new_id = self._combo.itemData(index)
        if new_id == self._active_id:
            return
        self._active_id = new_id
        self._refresh_content()
        if emit:
            self.selection_changed.emit(self._active_id)

    def set_params(self, params: dict[str, Any]) -> None:
        """Push params into the active detector's settings widgets silently."""
        det = self.active_detector()
        if det is None or self._settings_block is None:
            return
        self._values[det.name].update(params)
        self._settings_block.set_values(self._values[det.name])

    def set_overlay_state(self, overlay_state: dict[str, dict[str, Any]]) -> None:
        """Merge ``overlay_state`` into the active detector's overlay state."""
        det = self.active_detector()
        if det is None:
            return
        for key, fields in overlay_state.items():
            if key in self._overlay_state[det.name]:
                self._overlay_state[det.name][key].update(fields)
        self._overlay_row.populate(self._overlay_state[det.name])

    def set_manual_host(self, widget: QWidget | None) -> None:
        """Register the widget shown when the user picks Manual."""
        self._manual_host = widget
        self._refresh_content()

    def attach_manual_host(self) -> QWidget | None:
        """Detach the manual host so the caller can re-parent it elsewhere."""
        host = self._manual_host
        self._manual_host = None
        return host

    def set_carry_state(self, carry_enabled: bool, override_available: bool) -> None:
        """Update the ROI row's Carry checkbox and Override button state."""
        if self._roi_row is None:
            return
        self._roi_row.set_carry_enabled(carry_enabled)
        self._roi_row.set_override_enabled(override_available)

    def set_roi_button_checked(self, checked: bool) -> None:
        """Set the ROI edit button's checked state."""
        if self._roi_row is not None:
            self._roi_row.set_roi_button_checked(checked)

    # ----- combo / reset / save handlers -----

    def _on_combo_changed(self, index: int) -> None:
        new_id = self._combo.itemData(index)
        if new_id == self._active_id:
            return
        self._active_id = new_id
        self._refresh_content()
        self.selection_changed.emit(self._active_id)

    def _on_reset_clicked(self) -> None:
        if self._active_id == MANUAL:
            self._manual_overlay_state = default_manual_overlay_state(self.kind)
            self._overlay_row.populate(self._manual_overlay_state)
            self.reset_requested.emit()
            return
        det = self.active_detector()
        if det is None:
            return
        self._values[det.name] = {s.name: s.default for s in det.settings}
        self._overlay_state[det.name] = default_overlay_state(self.kind, det.overlays)
        if self._settings_block is not None:
            self._settings_block.set_values(self._values[det.name])
        self._overlay_row.populate(self._overlay_state[det.name])
        self.params_changed.emit(dict(self._values[det.name]))
        self.reset_requested.emit()

    def _on_save_default_clicked(self) -> None:
        if self.active_detector() is None:
            return
        reply = QMessageBox.question(
            self,
            f"Save {self.kind.capitalize()} default?",
            f"Replace this project's saved {self.kind} detector defaults with the current values?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.save_default_requested.emit()

    def _on_overlay_changed(self, key: str, field: str, value: object) -> None:
        if self._active_id == MANUAL:
            if key in self._manual_overlay_state:
                self._manual_overlay_state[key][field] = value
                self.overlay_changed.emit(key, field, value)
            return
        det = self.active_detector()
        if det is None:
            return
        self._overlay_state[det.name][key][field] = value
        self.overlay_changed.emit(key, field, value)

    def _on_setting_changed(self, name: str, value: SettingValue) -> None:
        det = self.active_detector()
        if det is None:
            return
        self._values[det.name][name] = value
        self.params_changed.emit(dict(self._values[det.name]))

    # ----- ROI row re-emitters -----

    def _on_roi_edit(self, active: bool) -> None:
        self.roi_edit_requested.emit(bool(active))

    def _on_roi_clear(self) -> None:
        self.clear_roi_requested.emit()

    def _on_roi_carry(self, enabled: bool) -> None:
        self.carry_roi_toggled.emit(bool(enabled))

    def _on_roi_override(self) -> None:
        self.override_roi_requested.emit()

    # ----- content rebuild -----

    def _refresh_content(self) -> None:
        # Manual host stays alive across rebuilds — it's owned externally
        # and only moves in / out of the content layout.
        if self._manual_host is not None and self._manual_host.parent() is self:
            self._manual_host.setParent(None)
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w is None or w is self._manual_host:
                continue
            w.deleteLater()
        self._settings_block = None
        self._roi_row = None

        det = self.active_detector()
        is_off = self._active_id == OFF
        is_manual = self._active_id == MANUAL

        # Overlay row + Reset apply to both Manual and any auto detector.
        # Save-as-default snapshots auto-detector params only — manual
        # styling lives entirely in card state today, no project-level
        # default to persist. Detect is only meaningful for auto detectors.
        has_content = is_manual or det is not None
        self._overlay_row.setVisible(has_content)
        self._reset_button.setEnabled(has_content)
        self._save_default_button.setEnabled(det is not None)
        self._detect_button.setVisible(det is not None)

        if is_off:
            return
        if is_manual:
            self._overlay_row.populate(self._manual_overlay_state)
            if self._manual_host is not None:
                self._content_layout.addWidget(self._manual_host)
                self._manual_host.setVisible(True)
            return
        if det is None:
            return

        self._overlay_row.populate(self._overlay_state[det.name])
        self._settings_block = SettingsBlock(
            det.settings,
            self._values[det.name],
            self._on_setting_changed,
            title="",
        )
        self._content_layout.addWidget(self._settings_block)

        if detector_has_roi(det) is not None:
            self._roi_row = _RoiRow(self.kind.capitalize())
            self._roi_row.roi_edit_requested.connect(self._on_roi_edit)
            self._roi_row.clear_roi_requested.connect(self._on_roi_clear)
            self._roi_row.carry_roi_toggled.connect(self._on_roi_carry)
            self._roi_row.override_roi_requested.connect(self._on_roi_override)
            self._content_layout.addWidget(self._roi_row)


# ---------------------------------------------------------------------------
# Manual host wrappers (the existing AnnotationGroup objects get re-parented
# into the matching card when "Manual" is picked)
# ---------------------------------------------------------------------------


def install_manual_hosts_into_cards(
    cards: dict[str, DetectorCard],
    manual_groups_by_target: dict[str, QWidget],
) -> None:
    """Hand each card the manual-annotation group widget for its kind."""
    for kind, card in cards.items():
        host = manual_groups_by_target.get(kind)
        if host is not None:
            card.set_manual_host(host)


def build_detector_cards(
    discovered_by_target: dict[str, list[Detector]],
    kinds: list[str],
) -> dict[str, DetectorCard]:
    """Build one :class:`DetectorCard` per kind."""
    return {kind: DetectorCard(kind, discovered_by_target.get(kind, [])) for kind in kinds}


__all__ = [
    "MANUAL",
    "OFF",
    "DetectorCard",
    "build_detector_cards",
    "default_overlay_state",
    "detector_has_roi",
    "install_manual_hosts_into_cards",
]
