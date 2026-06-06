"""DetectorPlugin lifecycle: wire DetectorCards to the orchestrator + persistence."""

from typing import TYPE_CHECKING, Any

import numpy as np
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QWidget

from eye_annotation_tool.auto_detectors.plugin import DetectorPlugin
from eye_annotation_tool.auto_detectors.plugin_loader import discover_plugins

from ..auto_detectors.orchestrator import DetectorOrchestrator
from ..gui.annotation_controls import AnnotationControlPanel
from ..gui.detector_card import MANUAL, OFF
from ..gui.image_viewer import ImageViewer
from ..state import CarryRoiStore, PerEyeStateStore, ProjectStore
from ..utils.project_settings import (
    CARRY_ROI_SLOTS,
    DETECTOR_OFF,
    KINDS,
)

if TYPE_CHECKING:
    from .binocular_controller import BinocularController

AUTO_DETECT_DEBOUNCE_MS = 0


def _roi_setting_name(detector: DetectorPlugin) -> str | None:
    """Return the name of the detector's ROI-typed setting (or ``None``)."""
    for s in detector.settings:
        if s.type == "roi":
            return s.name
    return None


def _strip_roi(params: dict, roi_setting_name: str | None) -> dict:
    """Return a copy of ``params`` with the ROI key dropped (used when persisting defaults)."""
    if roi_setting_name is None or roi_setting_name not in params:
        return dict(params)
    cleaned = dict(params)
    cleaned.pop(roi_setting_name)
    return cleaned


class DetectionController(QObject):
    """Bridge between the per-kind :class:`DetectorCard` widgets and the orchestrator."""

    annotation_modified = pyqtSignal(bool)
    status_message = pyqtSignal(str, int)

    def __init__(
        self,
        orchestrator: DetectorOrchestrator,
        per_eye_state: PerEyeStateStore,
        carry_roi_state: CarryRoiStore,
        project_store: ProjectStore,
        image_viewer: ImageViewer,
        annotation_controls: AnnotationControlPanel,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.orchestrator = orchestrator
        self.per_eye_state = per_eye_state
        self.carry_roi_state = carry_roi_state
        self.project_store = project_store
        self.image_viewer = image_viewer
        self.annotation_controls = annotation_controls
        self._binocular: BinocularController | None = None

        self._detectors_by_kind_id: dict[tuple[str, str], DetectorPlugin] = {
            (d.kind, d.name): d for d in discover_plugins()
        }

        self._pending_run_one: tuple[str, dict] | None = None
        self._auto_detect_debounce = QTimer(self)
        self._auto_detect_debounce.setSingleShot(True)
        self._auto_detect_debounce.setInterval(AUTO_DETECT_DEBOUNCE_MS)
        self._auto_detect_debounce.timeout.connect(self._on_auto_detect_debounce_fired)

        self.orchestrator.detector_ready.connect(self._on_detector_ready)
        self.orchestrator.detector_failed.connect(self._on_detector_failed)
        self.image_viewer.target_roi_changed.connect(self._on_target_roi_changed)

        self._wire_card_signals()

    def bind_binocular_controller(self, binocular_controller: "BinocularController") -> None:
        self._binocular = binocular_controller

    @property
    def binocular(self) -> "BinocularController":
        if self._binocular is None:
            raise RuntimeError("DetectionController.bind_binocular_controller was not called")
        return self._binocular

    # ---------------------------------------------------------------------------
    # Public callable surface used by image_viewer + tests
    # ---------------------------------------------------------------------------

    def overlay_state_lookup(self, kind: str) -> dict[str, dict] | None:
        """Return the active detector's overlay state for ``kind`` (None when off / manual)."""
        card = self.annotation_controls.card(kind)
        if card is None:
            return None
        return card.overlay_state()

    def enabled_detector(self, kind: str) -> DetectorPlugin | None:
        card = self.annotation_controls.card(kind)
        return card.active_detector() if card is not None else None

    def panel_for_kind(self, kind: str) -> QWidget | None:
        return self.annotation_controls.card(kind)

    def detector_default_params(self, kind: str) -> dict:
        det = self.enabled_detector(kind)
        if det is None:
            return {}
        return {s.name: s.default for s in det.settings}

    # ---------------------------------------------------------------------------
    # Card signal wiring
    # ---------------------------------------------------------------------------

    def _wire_card_signals(self) -> None:
        for kind in KINDS:
            card = self.annotation_controls.card(kind)
            if card is None:
                continue
            card.selection_changed.connect(
                lambda slug, k=kind: self._on_card_selection_changed(k, slug),
            )
            card.params_changed.connect(
                lambda params, k=kind: self._on_card_params_changed(k, params),
            )
            card.overlay_changed.connect(self._on_overlay_changed)
            card.reset_requested.connect(
                lambda k=kind: self._on_card_reset(k),
            )
            card.save_default_requested.connect(
                lambda k=kind: self._on_card_save_default(k),
            )
            card.roi_edit_requested.connect(
                lambda active, k=kind: self._on_roi_edit_requested(k, active),
            )
            card.clear_roi_requested.connect(
                lambda k=kind: self._on_clear_roi_requested(k),
            )
            card.carry_roi_toggled.connect(
                lambda enabled, k=kind: self._on_carry_roi_toggled(k, enabled),
            )
            card.override_roi_requested.connect(
                lambda k=kind: self._on_override_roi_requested(k),
            )
            card.detect_requested.connect(
                lambda k=kind: self._kick_live_run_for_kind(k),
            )

    # ---------------------------------------------------------------------------
    # Project load / apply
    # ---------------------------------------------------------------------------

    def apply_project_settings(self, detectors_block: dict) -> None:
        """Push the project's detectors block into the cards + orchestrator."""
        for kind in KINDS:
            entry = detectors_block.get(kind) or {}
            slug = entry.get("id", DETECTOR_OFF)
            params_by_slot = entry.get("params") or {}
            card = self.annotation_controls.card(kind)
            if card is None:
                continue
            card.set_selection(slug, emit=False)
            det = card.active_detector()
            if det is not None:
                self._restore_card_params(kind, det, params_by_slot)
            self.carry_roi_state.load_from_project_block(kind, entry.get("carry_roi") or {})
        self._refresh_orchestrator_enabled()
        self._refresh_auto_managed_kinds()
        self._kick_live_run_for_all_enabled()

    def _restore_card_params(self, kind: str, det: DetectorPlugin, params_by_slot: dict) -> None:
        active_slot = self._active_slot()
        for slot in CARRY_ROI_SLOTS:
            slot_params = params_by_slot.get(slot) if isinstance(params_by_slot, dict) else None
            if isinstance(slot_params, dict):
                self.per_eye_state.set_project_default(kind, slot, dict(slot_params))
        defaults = self.per_eye_state.get_project_default(kind, active_slot)
        card = self.annotation_controls.card(kind)
        if card is None:
            return
        if defaults:
            card.set_params(defaults)
        else:
            card.set_params({s.name: s.default for s in det.settings})

    # ---------------------------------------------------------------------------
    # Card signal handlers
    # ---------------------------------------------------------------------------

    def _on_card_selection_changed(self, kind: str, slug: str) -> None:
        self._persist_kind_id(kind, slug)
        self._refresh_orchestrator_enabled()
        self._refresh_auto_managed_kinds()
        # Switching the kind's detector invalidates whatever overlay /
        # ROI / cached result was being shown for it.
        self.image_viewer.clear_detection_overlay(kind)
        self.image_viewer.clear_target_roi(kind)
        self.orchestrator.set_cached_result(kind, None)
        self.annotation_modified.emit(True)
        self._kick_live_run(kind)

    def _on_card_params_changed(self, kind: str, params: dict) -> None:
        self.annotation_modified.emit(True)
        self.per_eye_state.set_params(self._active_slot(), kind, dict(params))
        # Single-shot 0 ms timer coalesces multiple slider events from
        # the same event-loop tick into one detection pass.
        self._pending_run_one = None
        self._auto_detect_debounce.start()

    def _on_card_reset(self, kind: str) -> None:
        card = self.annotation_controls.card(kind)
        if card is None or card.active_detector() is None:
            return
        self.image_viewer.clear_detection_overlay(kind)
        self.image_viewer.clear_target_roi(kind)
        self.orchestrator.set_cached_result(kind, None)
        self._kick_live_run(kind)

    def _on_card_save_default(self, kind: str) -> None:
        card = self.annotation_controls.card(kind)
        det = card.active_detector() if card is not None else None
        if card is None or det is None:
            return
        roi_name = _roi_setting_name(det)
        cleaned = _strip_roi(card.current_params(), roi_name)
        for slot in CARRY_ROI_SLOTS:
            self.per_eye_state.set_project_default(kind, slot, dict(cleaned))
        detectors_block = self.project_store.project.setdefault("detectors", {})
        kind_block = detectors_block.setdefault(kind, {})
        kind_block["id"] = card.active_id()
        kind_block.setdefault("params", {})
        for slot in CARRY_ROI_SLOTS:
            kind_block["params"][slot] = dict(cleaned)
        kind_block["carry_roi"] = self.carry_roi_state.to_project_block(kind)
        self.project_store.persist()
        self.status_message.emit(f"{kind.capitalize()} defaults saved.", 3000)

    def _on_overlay_changed(self, _key: str, _field: str, _value: Any) -> None:
        # The card already mutated its overlay state before emitting;
        # the canvas reads through the same lookup, so a repaint is enough.
        self.image_viewer.update_image()

    # ---------------------------------------------------------------------------
    # Run dispatch
    # ---------------------------------------------------------------------------

    def _on_auto_detect_debounce_fired(self) -> None:
        self._pending_run_one = None
        self._kick_live_run_for_all_enabled()

    def _kick_live_run(self, _kind: str) -> None:
        """Re-run every enabled detector top-down."""
        self._kick_live_run_for_all_enabled()

    def _kick_live_run_for_kind(self, kind: str) -> None:
        """Re-run just ``kind``'s detector against the cached upstream results."""
        card = self.annotation_controls.card(kind)
        if card is None or card.active_detector() is None:
            return
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return
        if kind == "pupil":
            self._run_pupil_with_crop(image, card.current_params())
            return
        self.orchestrator.run_one(kind, image, card.current_params())

    def _kick_live_run_for_all_enabled(self) -> None:
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return
        pupil_card = self.annotation_controls.card("pupil")
        if pupil_card is not None and pupil_card.active_detector() is not None:
            self._run_pupil_with_crop(image, pupil_card.current_params())
        else:
            self.orchestrator.set_cached_result("pupil", None)
        # Glint / limbus / eyelid run on the full image; pupil coords
        # cached above are already in full-image space so downstream
        # search regions hit the right eye.
        for kind in ("glint", "limbus", "eyelid"):
            card = self.annotation_controls.card(kind)
            if card is not None and card.active_detector() is not None:
                self.orchestrator.run_one(kind, image, card.current_params())

    def refresh_all_detections(self) -> None:
        """Public hook: re-run every enabled detector top-down on the current image."""
        self._kick_live_run_for_all_enabled()

    # ---------------------------------------------------------------------------
    # Binocular pupil crop
    # ---------------------------------------------------------------------------

    def _run_pupil_with_crop(self, image: np.ndarray, params: dict) -> None:
        """Run the pupil detector on the active eye's half in binocular mode.

        Without cropping the threshold detector sees both eyes and can
        latch onto a contour that straddles the divider line.
        """
        bounds = self._active_eye_crop_bounds(image)
        if bounds is None:
            self.orchestrator.run_one("pupil", image, params)
            return
        dx, dy, dw, dh = bounds
        cropped = image[dy : dy + dh, dx : dx + dw]
        cropped_params = dict(params)
        if "pupil_roi" in cropped_params:
            cropped_params["pupil_roi"] = _intersect_roi_with_crop(cropped_params["pupil_roi"], bounds)
        self.orchestrator.run_one(
            "pupil",
            cropped,
            cropped_params,
            post_process=lambda r: _translate_result(r, dx, dy),
        )

    def _active_eye_crop_bounds(self, image: np.ndarray) -> tuple[int, int, int, int] | None:
        """Return ``(dx, dy, dw, dh)`` for the active eye's half, or ``None`` in monocular."""
        if self._binocular is None or not self.binocular.is_binocular:
            return None
        full_h, full_w = image.shape[:2]
        divider_x = round(self.binocular.effective_divider_x_norm() * full_w)
        divider_x = max(1, min(full_w - 1, divider_x))
        if self.image_viewer.current_eye == "left":
            return (0, 0, divider_x, full_h)
        return (divider_x, 0, full_w - divider_x, full_h)

    def _on_detector_ready(self, kind: str, result: dict) -> None:
        active_slot = self._active_slot()
        self.image_viewer.set_detection_overlay(kind, result, eye_slot=active_slot)
        self.per_eye_state.set_result(active_slot, kind, result)

    def _on_detector_failed(self, kind: str) -> None:
        active_slot = self._active_slot()
        self.image_viewer.clear_detection_overlay(kind, eye_slot=active_slot)
        self.per_eye_state.set_result(active_slot, kind, None)
        self.status_message.emit(f"Auto Detect: {kind} failed at current parameters.", 5000)

    # ---------------------------------------------------------------------------
    # ROI affordance handlers
    # ---------------------------------------------------------------------------

    def _on_roi_edit_requested(self, kind: str, active: bool) -> None:
        self.image_viewer.set_active_roi_target(kind if active else None)

    def _on_clear_roi_requested(self, kind: str) -> None:
        self.image_viewer.set_target_roi(kind, None)
        card = self.annotation_controls.card(kind)
        det = card.active_detector() if card is not None else None
        if card is None or det is None:
            return
        roi_name = _roi_setting_name(det)
        if roi_name is not None:
            params = card.current_params()
            params[roi_name] = None
            card.set_params({roi_name: None})
            self._on_card_params_changed(kind, params)

    def _on_target_roi_changed(self, kind: str, roi: tuple | None) -> None:
        """Push a canvas-drawn ROI into the card params and re-run the detector.

        A canvas edit means the user is tuning this image's ROI specifically,
        so Carry auto-disables for the active eye when a rectangle lands.
        """
        card = self.annotation_controls.card(kind)
        det = card.active_detector() if card is not None else None
        if card is None or det is None:
            return
        roi_name = _roi_setting_name(det)
        if roi_name is None:
            return
        card.set_params({roi_name: roi})
        self._on_card_params_changed(kind, card.current_params())
        active_slot = self._active_slot()
        if roi is not None and self.carry_roi_state.is_enabled(kind, active_slot):
            self.carry_roi_state.set_enabled(kind, active_slot, False)
            self._persist_carry_roi(kind)
            self._refresh_carry_state_for(kind)

    def _on_carry_roi_toggled(self, kind: str, enabled: bool) -> None:
        active_slot = self._active_slot()
        self.carry_roi_state.set_enabled(kind, active_slot, enabled)
        if enabled:
            current_roi = self.image_viewer.get_target_roi(kind)
            if current_roi is not None:
                self.carry_roi_state.set_value(kind, active_slot, current_roi)
        self._persist_carry_roi(kind)
        self._refresh_carry_state_for(kind)

    def _on_override_roi_requested(self, kind: str) -> None:
        active_slot = self._active_slot()
        carry_value = self.carry_roi_state.get_value(kind, active_slot)
        if carry_value is None:
            return
        card = self.annotation_controls.card(kind)
        det = card.active_detector() if card is not None else None
        if card is None or det is None:
            return
        self.image_viewer.set_target_roi(kind, tuple(carry_value), eye_slot=active_slot)
        roi_name = _roi_setting_name(det)
        if roi_name is not None:
            card.set_params({roi_name: tuple(carry_value)})
            self._on_card_params_changed(kind, card.current_params())
        self._refresh_carry_state_for(kind)

    # ---------------------------------------------------------------------------
    # Per-image detection block round-trip
    # ---------------------------------------------------------------------------

    def collect_detections_for_save(self) -> dict:
        """Build the per-image ``detections`` dict from cached per-eye results."""
        active_slot = self._active_slot()
        self.per_eye_state.snapshot_orchestrator(active_slot, self.orchestrator)
        self.per_eye_state.snapshot_panel(active_slot, self.panel_for_kind)
        binocular = self.binocular.is_binocular
        out: dict = {}
        for kind in KINDS:
            det = self.enabled_detector(kind)
            if det is None:
                continue
            if binocular:
                per_eye_block: dict = {}
                for slot in ("left", "right"):
                    result = self.per_eye_state.get_result(slot, kind)
                    if result is None:
                        continue
                    params = self.per_eye_state.get_params(slot, kind) or {s.name: s.default for s in det.settings}
                    per_eye_block[slot] = {
                        "params": params,
                        "result": _serialize_result(result),
                    }
                if per_eye_block:
                    out[kind] = {"id": det.name, **per_eye_block}
            else:
                result = self.per_eye_state.get_result("single", kind)
                if result is None:
                    continue
                params = self.per_eye_state.get_params("single", kind) or {s.name: s.default for s in det.settings}
                out[kind] = {
                    "id": det.name,
                    "params": params,
                    "result": _serialize_result(result),
                }
        return out

    def apply_loaded_detections(self, detections: dict) -> None:
        """Restore per-image detection blocks from a loaded annotation file."""
        self.image_viewer.pause_updates()
        try:
            active_slot = self._active_slot()
            for kind, block in detections.items():
                det = self.enabled_detector(kind)
                if det is None or not isinstance(block, dict):
                    continue
                per_eye_params, per_eye_results = _extract_loaded_blob(block)
                roi_name = _roi_setting_name(det)
                for slot, params in per_eye_params.items():
                    if params is None:
                        continue
                    self.per_eye_state.set_params(slot, kind, dict(params))
                    if roi_name is not None and params.get(roi_name) is not None:
                        self.image_viewer.set_target_roi(kind, tuple(params[roi_name]), eye_slot=slot)
                for slot, result in per_eye_results.items():
                    self.per_eye_state.set_result(slot, kind, result)
                    if result is not None:
                        self.image_viewer.set_detection_overlay(kind, result, eye_slot=slot)
                active_params = per_eye_params.get(active_slot)
                if active_params is not None:
                    card = self.annotation_controls.card(kind)
                    if card is not None:
                        card.set_params(active_params)
                active_result = per_eye_results.get(active_slot)
                if active_result is not None:
                    self.orchestrator.set_cached_result(kind, active_result)
            self.per_eye_state.restore_panel(active_slot, self.panel_for_kind, self.detector_default_params)
        finally:
            self.image_viewer.resume_updates()

    # ---------------------------------------------------------------------------
    # Eye-switch hooks (called by BinocularController)
    # ---------------------------------------------------------------------------

    def on_active_eye_changed(self) -> None:
        """Re-apply per-eye saved params + repaint after the active eye switches."""
        active_slot = self._active_slot()
        self.per_eye_state.restore_panel(active_slot, self.panel_for_kind, self.detector_default_params)
        for kind in KINDS:
            self._refresh_carry_state_for(kind)
        self._kick_live_run_for_all_enabled()

    def cancel_active_roi_edit(self) -> None:
        """Drop any in-progress ROI drag-edit toggle (called when leaving a context)."""
        self.image_viewer.set_active_roi_target(None)
        for kind in KINDS:
            card = self.annotation_controls.card(kind)
            if card is not None:
                card.set_roi_button_checked(False)

    def clear_all(self) -> None:
        """Drop every detection result + every kind's ROI on the current image."""
        self._auto_detect_debounce.stop()
        self._pending_run_one = None
        for kind in KINDS:
            self.orchestrator.set_cached_result(kind, None)
            self.image_viewer.clear_detection_overlay(kind)
            self.image_viewer.clear_target_roi(kind)
        self.per_eye_state.clear_all()
        self.image_viewer.set_active_roi_target(None)
        self.annotation_modified.emit(True)

    # ---------------------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------------------

    def _refresh_orchestrator_enabled(self) -> None:
        per_kind: dict[str, DetectorPlugin | None] = {kind: self.enabled_detector(kind) for kind in KINDS}
        self.orchestrator.set_enabled_detectors(per_kind)

    def _refresh_auto_managed_kinds(self) -> None:
        auto_kinds: set[str] = set()
        for kind in KINDS:
            card = self.annotation_controls.card(kind)
            if card is None:
                continue
            if card.active_id() not in (OFF, MANUAL):
                auto_kinds.add(kind)
        self.image_viewer.set_auto_managed_targets(auto_kinds)

    def _persist_kind_id(self, kind: str, slug: str) -> None:
        detectors_block = self.project_store.project.setdefault("detectors", {})
        kind_block = detectors_block.setdefault(kind, {})
        kind_block["id"] = slug
        kind_block.setdefault("params", {})
        kind_block.setdefault("carry_roi", self.carry_roi_state.to_project_block(kind))
        self.project_store.persist()

    def _persist_carry_roi(self, kind: str) -> None:
        detectors_block = self.project_store.project.setdefault("detectors", {})
        kind_block = detectors_block.setdefault(kind, {})
        kind_block["carry_roi"] = self.carry_roi_state.to_project_block(kind)
        self.project_store.persist()

    def _refresh_carry_state_for(self, kind: str) -> None:
        card = self.annotation_controls.card(kind)
        if card is None:
            return
        active_slot = self._active_slot()
        carry_enabled = self.carry_roi_state.is_enabled(kind, active_slot)
        carry_value = self.carry_roi_state.get_value(kind, active_slot)
        card.set_carry_state(carry_enabled, carry_value is not None)

    def _active_slot(self) -> str:
        if self._binocular is None:
            return "single"
        return self.binocular.active_eye_slot()


# ---------------------------------------------------------------------------
# Generic serialisation helpers
# ---------------------------------------------------------------------------


def _serialize_result(result: object):
    """Recursively convert numpy / tuples / contours into JSON-friendly types.

    Mask arrays (any 2-D uint8 ndarray) are dropped — they are transient
    visualisation data and should not bloat the per-image annotation.
    """
    import numpy as np

    if isinstance(result, dict):
        out: dict = {}
        for k, v in result.items():
            if k == "mask":
                continue
            out[k] = _serialize_result(v)
        return out
    if isinstance(result, list):
        return [_serialize_result(v) for v in result]
    if isinstance(result, tuple):
        return [_serialize_result(v) for v in result]
    if isinstance(result, np.ndarray):
        if result.ndim == 2 and result.dtype == np.uint8:
            # 2-D uint8 = mask; drop.
            return None
        return result.tolist()
    if isinstance(result, np.integer):
        return int(result)
    if isinstance(result, np.floating):
        return float(result)
    return result


def _intersect_roi_with_crop(
    roi: tuple | list | None,
    crop: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    """Translate a full-image ROI into crop coords, or ``None`` if it falls outside."""
    if roi is None:
        return None
    rx, ry, rw, rh = roi
    cx, cy, cw, ch = crop
    ix = max(rx, cx)
    iy = max(ry, cy)
    ex = min(rx + rw, cx + cw)
    ey = min(ry + rh, cy + ch)
    iw = ex - ix
    ih = ey - iy
    if iw <= 0 or ih <= 0:
        return None
    return (int(ix - cx), int(iy - cy), int(iw), int(ih))


def _translate_result(result: dict, dx: int, dy: int) -> dict:
    """Shift every (x, y) field in a detector result by ``(dx, dy)``.

    Handles the cheshm-standard fields ``center`` / ``ellipse`` /
    ``contour`` recursively, plus glints stored under ``glints``.
    Other keys pass through untouched.
    """
    if dx == 0 and dy == 0:
        return result
    out: dict = {}
    for key, value in result.items():
        if key == "center" and value is not None:
            out[key] = (float(value[0]) + dx, float(value[1]) + dy)
        elif key == "ellipse" and value is not None:
            (ecx, ecy), (ew, eh), angle = value
            out[key] = ((float(ecx) + dx, float(ecy) + dy), (ew, eh), angle)
        elif key == "contour" and isinstance(value, np.ndarray):
            shifted = value.copy()
            shifted[..., 0] += int(dx)
            shifted[..., 1] += int(dy)
            out[key] = shifted
        elif key == "glints" and isinstance(value, list):
            out[key] = [_translate_result(g, dx, dy) for g in value]
        else:
            out[key] = value
    return out


def _extract_loaded_blob(blob: dict) -> tuple[dict[str, dict | None], dict[str, dict | None]]:
    """Normalise an on-disk per-kind detection block to per-eye ``(params, results)`` maps."""
    if "params" in blob or "result" in blob:
        params = blob.get("params") or None
        result = blob.get("result") or None
        return {"single": params}, {"single": result}
    per_eye_params: dict[str, dict | None] = {}
    per_eye_results: dict[str, dict | None] = {}
    for slot in ("left", "right"):
        entry = blob.get(slot)
        if not isinstance(entry, dict):
            continue
        per_eye_params[slot] = entry.get("params") or None
        per_eye_results[slot] = entry.get("result") or None
    return per_eye_params, per_eye_results
