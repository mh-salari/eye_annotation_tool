"""DetectorPlugin lifecycle: wire DetectorCards to the orchestrator + persistence."""

from itertools import starmap
from typing import TYPE_CHECKING

import numpy as np
from PyQt5.QtCore import QObject, QPointF, QSizeF, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget

from eye_annotation_tool.auto_detectors.plugin import DetectorPlugin
from eye_annotation_tool.auto_detectors.plugin_loader import discover_plugins

from ..auto_detectors.orchestrator import DetectorOrchestrator
from ..gui.annotation_controls import AnnotationControlPanel
from ..gui.detector_card import MANUAL, OFF
from ..gui.image_viewer import ImageViewer
from ..state import PerEyeStateStore, ProjectStore
from ..utils.project_settings import (
    DETECTOR_OFF,
    EYE_SLOTS,
    KINDS,
)

if TYPE_CHECKING:
    from .binocular_controller import BinocularController

AUTO_DETECT_DEBOUNCE_MS = 0

# Manual annotation fields per kind, mirrored between the image viewer's eye
# data and the unified per-eye detections blocks.
_MANUAL_POINTS_FIELD = {
    "pupil": "pupil_points",
    "limbus": "limbus_points",
    "glint": "glint_points",
    "eyelid": "eyelid_contour_points",
}
_MANUAL_ELLIPSE_FIELD = {"pupil": "pupil_ellipse", "limbus": "limbus_ellipse"}
_MANUAL_CURVE_FIELD = {"pupil": "pupil_fit_curve", "limbus": "limbus_fit_curve"}

# Detector kind -> the canvas annotation slug clicks place points for.
_ANNOTATION_SLUG = {"pupil": "pupil", "limbus": "limbus", "glint": "glint", "eyelid": "eyelid_contour"}


def _manual_result(kind: str, eye_block: dict) -> dict | None:
    """Canonical manual result for ``kind``.

    Pupil and limbus report the fitted ellipse and, when present, the smooth
    boundary curve. Glint reports one ``{center}`` entry per clicked point, so a
    manual glint and a detector glint are both read from ``result.glints``.
    Eyelid stays points-only — its annotation is the points stored in ``params``.
    """
    if kind == "glint":
        points = eye_block.get(_MANUAL_POINTS_FIELD["glint"]) or []
        if not points:
            return None
        return {"glints": [{"center": [p.x(), p.y()]} for p in points], "search_area": None}
    field = _MANUAL_ELLIPSE_FIELD.get(kind)
    if field is None:
        return None
    ellipse = eye_block.get(field)
    if not ellipse:
        return None
    center, size, angle = ellipse
    result = {
        "center": [center.x(), center.y()],
        "ellipse": [[center.x(), center.y()], [size.width(), size.height()], float(angle)],
    }
    curve = eye_block.get(_MANUAL_CURVE_FIELD[kind])
    if curve:
        result["boundary"] = [[p.x(), p.y()] for p in curve]
    return result


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
        project_store: ProjectStore,
        image_viewer: ImageViewer,
        annotation_controls: AnnotationControlPanel,
        parent: QObject | None = None,
    ) -> None:
        """Wire the detector cards, orchestrator, and per-eye stores together."""
        super().__init__(parent)
        self.orchestrator = orchestrator
        self.per_eye_state = per_eye_state
        self.project_store = project_store
        self.image_viewer = image_viewer
        self.annotation_controls = annotation_controls
        self._binocular: BinocularController | None = None
        # Shared undo/redo timeline; injected by MainWindow after construction.
        self.undo_coordinator = None

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
        self.image_viewer.active_roi_delete_requested.connect(self._on_roi_delete_requested)
        self.image_viewer.roi_edit_activated.connect(self._on_roi_edit_activated)
        self.image_viewer.points_activated.connect(self._on_points_activated)
        self.image_viewer.interaction_deactivated.connect(self._on_interaction_deactivated)
        self.annotation_controls.points_active_toggled.connect(self._on_points_edit_requested)

        self._wire_card_signals()

    def bind_binocular_controller(self, binocular_controller: "BinocularController") -> None:
        """Store the binocular controller used for active-eye queries."""
        self._binocular = binocular_controller

    @property
    def binocular(self) -> "BinocularController":
        """Return the bound binocular controller, raising if unbound."""
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
        """Return the active detector for ``kind``, or ``None``."""
        card = self.annotation_controls.card(kind)
        return card.active_detector() if card is not None else None

    def panel_for_kind(self, kind: str) -> QWidget | None:
        """Return the detector card widget for ``kind``."""
        return self.annotation_controls.card(kind)

    def detector_default_params(self, kind: str) -> dict:
        """Return the default param values for ``kind``'s active detector."""
        det = self.enabled_detector(kind)
        if det is None:
            return {}
        return {s.name: s.default for s in det.settings}

    def snapshot_params(self) -> dict[str, dict]:
        """Return the active eye's current params for every enabled detector kind."""
        out: dict[str, dict] = {}
        for kind in KINDS:
            card = self.annotation_controls.card(kind)
            if card is not None and card.active_detector() is not None:
                out[kind] = dict(card.current_params())
        return out

    def snapshot_selection(self) -> dict[str, str]:
        """Return each kind's active detector id (Off / Manual / detector name) for undo/redo."""
        out: dict[str, str] = {}
        for kind in KINDS:
            card = self.annotation_controls.card(kind)
            if card is not None:
                out[kind] = card.active_id()
        return out

    def apply_selection(self, selection: dict[str, str]) -> None:
        """Restore each kind's detector selection for undo/redo.

        Silent (``emit=False``) so it records no new undo step; stale overlays,
        ROIs, and cached results for changed kinds are dropped and the enabled
        set is refreshed. The caller re-runs detection afterwards (via
        :meth:`apply_params`), so this does not run detectors itself.
        """
        for kind in KINDS:
            slug = selection.get(kind)
            card = self.annotation_controls.card(kind)
            if card is None or slug is None or card.active_id() == slug:
                continue
            card.set_selection(slug, emit=False)
            self.image_viewer.clear_detection_overlay(kind)
            self.image_viewer.clear_target_roi(kind)
            self.orchestrator.set_cached_result(kind, None)
        self._refresh_orchestrator_enabled()
        self._refresh_auto_managed_kinds()

    def apply_params(self, params_by_kind: dict[str, dict]) -> None:
        """Push ``params_by_kind`` onto the active eye's cards + state and re-run.

        Used by undo/redo and by paste-settings. Does not record a new undo
        step — :meth:`DetectorCard.set_params` is silent, so no param-change
        signal fires back into the coordinator.
        """
        active_slot = self._active_slot()
        for kind, params in params_by_kind.items():
            card = self.annotation_controls.card(kind)
            det = card.active_detector() if card is not None else None
            if card is None or det is None:
                continue
            card.set_params(dict(params))
            self.per_eye_state.set_params(active_slot, kind, dict(params))
            # The visual ROI rectangle lives outside the undo snapshot, so mirror
            # it from the restored param value; otherwise an undone ROI draw would
            # revert the detector crop but leave the rectangle on the canvas.
            roi_name = _roi_setting_name(det)
            if roi_name is not None:
                self.image_viewer.set_target_roi(kind, params.get(roi_name), eye_slot=active_slot)
        self._kick_live_run_for_all_enabled()

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
            card.overlay_changed.connect(
                lambda key, field, value, k=kind: self._on_overlay_changed(k, key, field, value),
            )
            card.reset_requested.connect(
                lambda k=kind: self._on_card_reset(k),
            )
            card.save_default_requested.connect(
                lambda k=kind: self._on_card_save_default(k),
            )
            card.pins_changed.connect(
                lambda pinned, k=kind: self._on_card_pins_changed(k, pinned),
            )
            card.roi_edit_requested.connect(
                lambda active, k=kind: self._on_roi_edit_requested(k, active),
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
            card.set_pinned(slug, entry.get("pinned") or [])
            # Seed every eye with the project default; per-image loads then
            # override per eye where the saved annotation says otherwise.
            for slot in EYE_SLOTS:
                self.per_eye_state.set_selection(slot, kind, slug)
            card.set_selection(slug, emit=False)
            overlays = entry.get("overlays")
            if isinstance(overlays, dict):
                card.set_all_overlay_states(_deserialize_overlays(overlays))
            det = card.active_detector()
            if det is not None:
                self._restore_card_params(kind, det, params_by_slot)
        self._refresh_orchestrator_enabled()
        self._refresh_auto_managed_kinds()
        self._kick_live_run_for_all_enabled()

    def _restore_card_params(self, kind: str, det: DetectorPlugin, params_by_slot: dict) -> None:
        active_slot = self._active_slot()
        for slot in EYE_SLOTS:
            slot_params = params_by_slot.get(slot) if isinstance(params_by_slot, dict) else None
            if isinstance(slot_params, dict):
                self.per_eye_state.set_project_default(kind, slot, dict(slot_params))
        defaults = self.per_eye_state.get_project_default(kind, active_slot)
        card = self.annotation_controls.card(kind)
        if card is None:
            return
        card.set_project_default(det.name, defaults)
        if defaults:
            card.set_params(defaults)
        else:
            card.set_params({s.name: s.default for s in det.settings})

    # ---------------------------------------------------------------------------
    # Card signal handlers
    # ---------------------------------------------------------------------------

    def _on_card_selection_changed(self, kind: str, slug: str) -> None:
        # Record the choice for the active eye only — each eye picks its own
        # detector. The project default is left untouched (it changes only via
        # Project Settings / save-as-default).
        self.per_eye_state.set_selection(self._active_slot(), kind, slug)
        self._refresh_orchestrator_enabled()
        self._refresh_auto_managed_kinds()
        # Switching the kind's detector invalidates whatever overlay /
        # ROI / cached result was being shown for it.
        self.image_viewer.clear_detection_overlay(kind)
        self.image_viewer.clear_target_roi(kind)
        self.orchestrator.set_cached_result(kind, None)
        # Switching detector (e.g. to Manual) must drop any active ROI edit so
        # clicks return to annotating instead of drawing an ROI.
        self.cancel_active_roi_edit()
        # Keep the project's per-detector overlay map in sync now that the
        # active detector changed; each detector keeps its own overlays.
        self._persist_overlays(kind)
        self.annotation_modified.emit(True)
        self._kick_live_run(kind)
        # A detector-type switch is a discrete edit: record it on the shared
        # timeline so undo/redo steps through it like points and params.
        if self.undo_coordinator is not None:
            self.undo_coordinator.capture()

    def _on_card_params_changed(self, kind: str, params: dict) -> None:
        self.annotation_modified.emit(True)
        self.per_eye_state.set_params(self._active_slot(), kind, dict(params))
        if self.undo_coordinator is not None:
            self.undo_coordinator.capture_debounced()
        card = self.annotation_controls.card(kind)
        if card is not None and card.active_id() == MANUAL:
            # Manual fit-setting change (mode / centre / smoothness): re-fit now.
            if self.image_viewer.refit_manual_live(kind):
                self._kick_live_run_for_all_enabled()
            return
        # Single-shot 0 ms timer coalesces multiple slider events from
        # the same event-loop tick into one detection pass.
        self._pending_run_one = None
        self._auto_detect_debounce.start()

    def manual_fit_params(self, kind: str) -> dict:
        """Active manual fit settings for ``kind`` (empty unless that kind is Manual)."""
        card = self.annotation_controls.card(kind)
        if card is None or card.active_id() != MANUAL:
            return {}
        return card.current_params()

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
        slots = ("left", "right") if self.binocular.is_binocular else ("single",)
        for slot in slots:
            self.per_eye_state.set_project_default(kind, slot, dict(cleaned))
        detectors_block = self.project_store.project.setdefault("detectors", {})
        kind_block = detectors_block.setdefault(kind, {})
        kind_block["id"] = card.active_id()
        kind_block["params"] = {slot: dict(cleaned) for slot in slots}
        kind_block["pinned"] = card.current_pinned()
        kind_block["overlays"] = _serialize_overlays(card.all_overlay_states())
        self.project_store.persist()
        card.set_project_default(card.active_id(), cleaned)
        self.status_message.emit(f"{kind.capitalize()} default saved.", 3000)

    def _on_overlay_changed(self, kind: str, _key: str, _field: str, _value: object) -> None:
        # Overlays are a project-level display preference: persist the change to
        # the project immediately, then repaint to reflect it.
        self._persist_overlays(kind)
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
            self._run_with_crop("pupil", image, card.current_params())
            return
        self.orchestrator.run_one(kind, image, card.current_params())

    def _kick_live_run_for_all_enabled(self) -> None:
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return
        pupil_card = self.annotation_controls.card("pupil")
        if pupil_card is not None and pupil_card.active_detector() is not None:
            self._run_with_crop("pupil", image, pupil_card.current_params())
        else:
            self.orchestrator.set_cached_result("pupil", self._manual_pupil_result())
        # Glint / limbus / eyelid run on the active eye's half too, anchored on
        # the pupil translated into that crop, so each kind stays on its side of
        # the divider instead of latching onto the other eye or the face.
        for kind in ("glint", "limbus", "eyelid"):
            card = self.annotation_controls.card(kind)
            if card is not None and card.active_detector() is not None:
                self._run_with_crop(kind, image, card.current_params())

    def refresh_all_detections(self) -> None:
        """Public hook: re-run every enabled detector top-down on the current image."""
        self._kick_live_run_for_all_enabled()

    def _manual_pupil_result(self) -> dict | None:
        """Pupil result from the manually-fitted pupil ellipse (or ``None``).

        Lets glint/limbus anchor on a manual pupil exactly like an auto one, as
        long as a valid ellipse (centre) exists.
        """
        pupil_card = self.annotation_controls.card("pupil")
        if pupil_card is None or pupil_card.active_id() != MANUAL:
            return None
        ellipse = self.image_viewer.pupil_ellipse
        if ellipse is None:
            return None
        center, size, angle = ellipse
        cx = center.x() if hasattr(center, "x") else center[0]
        cy = center.y() if hasattr(center, "y") else center[1]
        w = size.width() if hasattr(size, "width") else size[0]
        h = size.height() if hasattr(size, "height") else size[1]
        return {"center": (round(cx), round(cy)), "ellipse": ((cx, cy), (w, h), angle)}

    def on_manual_edited(self) -> None:
        """Live-update on a manual point edit: re-fit the active kind, re-run dependents.

        Re-fits only once a fit already exists for the kind (so editing before
        the first explicit fit doesn't auto-fit). Applies to manual pupil and
        limbus alike.
        """
        kind = self.image_viewer.current_annotation
        if kind not in {"pupil", "limbus"}:
            return
        card = self.annotation_controls.card(kind)
        if card is None or card.active_id() != MANUAL:
            return
        if not self.image_viewer.refit_manual_live(kind):
            return
        self._kick_live_run_for_all_enabled()

    def refit_manual_curves(self) -> None:
        """Rebuild the active eye's manual ellipse + smooth curve after load / eye switch."""
        for kind in ("pupil", "limbus"):
            card = self.annotation_controls.card(kind)
            if card is not None and card.active_id() == MANUAL:
                self.image_viewer.refit_manual_live(kind)

    # ---------------------------------------------------------------------------
    # Binocular pupil crop
    # ---------------------------------------------------------------------------

    def _run_with_crop(self, kind: str, image: np.ndarray, params: dict) -> None:
        """Run ``kind``'s detector on the active eye's half in binocular mode.

        Cropping to the eye's side keeps every detector on its own side of the
        divider: without it a threshold detector sees both eyes and can latch
        onto a contour that straddles the divider, or a glint with no pupil
        anchor onto a bright spot anywhere on the face.

        The detector's ROI is intersected with the crop, the cached pupil
        (which glint/limbus/eyelid anchor on) is translated into the crop's
        coordinates for the run, and the result is translated back to full-image
        coordinates. In monocular mode the detector runs on the whole image.
        """
        bounds = self._active_eye_crop_bounds(image)
        if bounds is None:
            self.orchestrator.run_one(kind, image, params)
            return
        dx, dy, dw, dh = bounds
        cropped = image[dy : dy + dh, dx : dx + dw]
        cropped_params = dict(params)
        roi_name = _roi_setting_name(self.orchestrator.enabled_detector(kind))
        if roi_name is not None and cropped_params.get(roi_name) is not None:
            cropped_params[roi_name] = _intersect_roi_with_crop(cropped_params[roi_name], bounds)
        full_pupil = self.orchestrator.cached_result("pupil")
        anchors_on_pupil = kind in {"glint", "limbus", "eyelid"} and full_pupil is not None
        if anchors_on_pupil:
            self.orchestrator.set_cached_result("pupil", _translate_result(full_pupil, -dx, -dy))
        try:
            self.orchestrator.run_one(
                kind,
                cropped,
                cropped_params,
                post_process=lambda r: _translate_result(r, dx, dy),
            )
        finally:
            if anchors_on_pupil:
                self.orchestrator.set_cached_result("pupil", full_pupil)

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
    # Interaction-mode handlers (one active: a kind's ROI, a kind's points, or none)
    # ---------------------------------------------------------------------------

    def _set_active_interaction(self, kind: str | None, kind_type: str | None) -> None:
        """Make ``(kind, kind_type)`` the single active interaction; turn off the rest.

        ``kind_type`` is ``"roi"``, ``"points"`` or ``None``. Exactly one ROI
        button or Add-points button ends up checked (none when ``kind_type`` is
        ``None``), and the image viewer's ROI target + point-edit flag are set
        to match. A points activation also points the canvas at that kind.
        """
        for k in KINDS:
            card = self.annotation_controls.card(k)
            if card is not None:
                card.set_roi_button_checked(k == kind and kind_type == "roi")
            group = self.annotation_controls.manual_group_for_kind(k)
            if group is not None:
                group.set_checked(k == kind and kind_type == "points")
        self.image_viewer.set_active_roi_target(kind if kind_type == "roi" else None)
        if kind_type == "points" and kind is not None:
            self.image_viewer.set_points_active(True, _ANNOTATION_SLUG[kind])
        else:
            self.image_viewer.set_points_active(False)

    def _on_roi_edit_requested(self, kind: str, active: bool) -> None:
        self._set_active_interaction(kind if active else None, "roi" if active else None)

    def _on_points_edit_requested(self, kind: str, active: bool) -> None:
        self._set_active_interaction(kind if active else None, "points" if active else None)

    def _on_roi_edit_activated(self, kind: str) -> None:
        """A direct click on a drawn ROI activated it; sync buttons + viewer state."""
        self._set_active_interaction(kind, "roi")

    def _on_points_activated(self, kind: str) -> None:
        """A direct click on a point activated that kind's points; sync state."""
        self._set_active_interaction(kind, "points")

    def _on_interaction_deactivated(self) -> None:
        """Escape left the active interaction; clear every ROI / Add-points button."""
        self._set_active_interaction(None, None)

    def _on_roi_delete_requested(self, kind: str) -> None:
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
        """Push a canvas-drawn ROI into the card params and re-run the detector."""
        card = self.annotation_controls.card(kind)
        det = card.active_detector() if card is not None else None
        if card is None or det is None:
            return
        roi_name = _roi_setting_name(det)
        if roi_name is None:
            return
        # A plain select/activate click lands in ROI drag mode and emits on
        # release even when nothing moved; skip the unchanged value so it
        # doesn't mark the image modified or re-run the detector for nothing.
        if card.current_params().get(roi_name) == roi:
            return
        card.set_params({roi_name: roi})
        self._on_card_params_changed(kind, card.current_params())

    # ---------------------------------------------------------------------------
    # Per-image detection block round-trip
    # ---------------------------------------------------------------------------

    def collect_detections_for_save(self) -> dict:
        """Build the per-image ``detections`` dict, one block per eye.

        Each block carries its own detector id, params and result. A slot is
        recorded when it has a detection result or its detector differs
        from the project default; otherwise it is omitted and falls back to the
        project default on load. This keeps each eye's choice independent.
        """
        active_slot = self._active_slot()
        self.per_eye_state.snapshot_orchestrator(active_slot, self.orchestrator)
        self.per_eye_state.snapshot_panel(active_slot, self.panel_for_kind)
        project_detectors = self.project_store.project.get("detectors", {})
        bino = self.binocular.is_binocular
        slots = ("left", "right") if bino else ("single",)
        slot_to_eye = {"left": "left", "right": "right"} if bino else {"single": "left"}
        eye_data = self.image_viewer.get_annotation_data()
        out: dict = {}
        for kind in KINDS:
            default_id = (project_detectors.get(kind) or {}).get("id", DETECTOR_OFF)
            kind_block: dict = {}
            for slot in slots:
                slug = self.per_eye_state.get_selection(slot, kind)
                if slug is None:
                    continue
                entry: dict = {"id": slug}
                det = self._detectors_by_kind_id.get((kind, slug))
                if det is not None:
                    result = self.per_eye_state.get_result(slot, kind)
                    if slug == default_id and result is None:
                        continue
                    entry["params"] = self.per_eye_state.get_params(slot, kind) or {
                        s.name: s.default for s in det.settings
                    }
                    entry["result"] = _serialize_result(result) if result is not None else None
                elif slug == MANUAL:
                    block = eye_data.get(slot_to_eye[slot], {})
                    points = block.get(_MANUAL_POINTS_FIELD[kind]) or []
                    if not points and slug == default_id:
                        continue
                    params = dict(self.per_eye_state.get_params(slot, kind) or {})
                    params["points"] = [[p.x(), p.y()] for p in points]
                    entry["params"] = params
                    entry["result"] = _manual_result(kind, block)
                elif slug == default_id:
                    continue
                kind_block[slot] = entry
            if kind_block:
                out[kind] = kind_block
        return out

    def _restore_loaded_slot(
        self,
        kind: str,
        slot: str,
        slot_to_eye: dict[str, str],
        params: dict | None,
        result: dict | None,
        eye_data: dict,
    ) -> None:
        """Restore one (kind, slot): manual -> eye data + fit params; detector -> params + result."""
        slug = self.per_eye_state.get_selection(slot, kind)
        if slug == MANUAL:
            eye = slot_to_eye.get(slot)
            if eye is None or params is None:
                return
            eye_data[eye][_MANUAL_POINTS_FIELD[kind]] = list(starmap(QPointF, params.get("points") or []))
            ellipse = (result or {}).get("ellipse")
            if ellipse and kind in _MANUAL_ELLIPSE_FIELD:
                (cx, cy), (w, h), angle = ellipse
                eye_data[eye][_MANUAL_ELLIPSE_FIELD[kind]] = (QPointF(cx, cy), QSizeF(w, h), angle)
            self.per_eye_state.set_params(slot, kind, {k: v for k, v in params.items() if k != "points"})
        elif slug != DETECTOR_OFF:
            if params is not None:
                self.per_eye_state.set_params(slot, kind, dict(params))
            if result is not None:
                self.per_eye_state.set_result(slot, kind, result)

    def apply_loaded_detections(self, detections: dict) -> None:
        """Restore per-image state from a loaded annotation's detections blocks.

        Detector slots restore their params + cached result; manual slots
        restore their points + ellipse into the image viewer's eye data and
        their fit settings into the per-eye params. Per-eye ids come from the
        saved block, falling back to the project default when absent.
        """
        self.image_viewer.pause_updates()
        try:
            project_detectors = self.project_store.project.get("detectors", {})
            active_slot = self._active_slot()
            bino = self.binocular.is_binocular
            slot_to_eye = {"left": "left", "right": "right"} if bino else {"single": "left"}
            eye_data: dict = {"left": {}, "right": {}}

            for kind in KINDS:
                card = self.annotation_controls.card(kind)
                if card is None:
                    continue
                block = detections.get(kind)
                ids, per_eye_params, per_eye_results = (
                    _extract_loaded_blob(block) if isinstance(block, dict) else ({}, {}, {})
                )
                default_id = (project_detectors.get(kind) or {}).get("id", DETECTOR_OFF)
                for slot in EYE_SLOTS:
                    self.per_eye_state.set_selection(slot, kind, ids.get(slot) or default_id)
                for slot in EYE_SLOTS:
                    self._restore_loaded_slot(
                        kind, slot, slot_to_eye, per_eye_params.get(slot), per_eye_results.get(slot), eye_data
                    )
                active_slug = self.per_eye_state.get_selection(active_slot, kind)
                if active_slug != card.active_id():
                    card.set_selection(active_slug, emit=False)

            self.image_viewer.set_annotation_data(eye_data)
            self._refresh_orchestrator_enabled()
            self._refresh_auto_managed_kinds()

            # Paint every eye's saved detector result + ROI, then bind the active
            # eye's cached result into the orchestrator for live re-runs.
            for kind in KINDS:
                det = self.enabled_detector(kind)
                roi_name = _roi_setting_name(det) if det is not None else None
                for slot in EYE_SLOTS:
                    params = self.per_eye_state.get_params(slot, kind)
                    if roi_name is not None and params is not None and params.get(roi_name) is not None:
                        self.image_viewer.set_target_roi(kind, tuple(params[roi_name]), eye_slot=slot)
                    result = self.per_eye_state.get_result(slot, kind)
                    if result is not None:
                        self.image_viewer.set_detection_overlay(kind, result, eye_slot=slot)
                active_result = self.per_eye_state.get_result(active_slot, kind)
                if active_result is not None:
                    self.orchestrator.set_cached_result(kind, active_result)
            self.per_eye_state.restore_panel(active_slot, self.panel_for_kind, self.detector_default_params)
            self.refit_manual_curves()
        finally:
            self.image_viewer.resume_updates()

    # ---------------------------------------------------------------------------
    # Eye-switch hooks (called by BinocularController)
    # ---------------------------------------------------------------------------

    def on_active_eye_changed(self) -> None:
        """Re-bind each card to the new eye's detector + params, then repaint."""
        active_slot = self._active_slot()
        self._restore_selection_for_slot(active_slot)
        self.per_eye_state.restore_panel(active_slot, self.panel_for_kind, self.detector_default_params)
        self.refit_manual_curves()
        self._kick_live_run_for_all_enabled()
        # Undo history is per (image, eye); seed it fresh for the new eye.
        if self.undo_coordinator is not None:
            self.undo_coordinator.reset()

    def _restore_selection_for_slot(self, slot: str) -> None:
        """Point every card at the detector ``slot`` chose (project default if unset)."""
        project_detectors = self.project_store.project.get("detectors", {})
        for kind in KINDS:
            card = self.annotation_controls.card(kind)
            if card is None:
                continue
            slug = self.per_eye_state.get_selection(slot, kind)
            if slug is None:
                slug = (project_detectors.get(kind) or {}).get("id", DETECTOR_OFF)
            if slug != card.active_id():
                card.set_selection(slug, emit=False)
        self._refresh_orchestrator_enabled()
        self._refresh_auto_managed_kinds()

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
            if card.active_id() not in {OFF, MANUAL}:
                auto_kinds.add(kind)
        self.image_viewer.set_auto_managed_targets(auto_kinds)

    def _persist_overlays(self, kind: str) -> None:
        card = self.annotation_controls.card(kind)
        if card is None:
            return
        detectors_block = self.project_store.project.setdefault("detectors", {})
        kind_block = detectors_block.setdefault(kind, {})
        kind_block["overlays"] = _serialize_overlays(card.all_overlay_states())
        self.project_store.persist()

    def _on_card_pins_changed(self, kind: str, pinned: list) -> None:
        detectors_block = self.project_store.project.setdefault("detectors", {})
        kind_block = detectors_block.setdefault(kind, {})
        kind_block["pinned"] = list(pinned)
        self.project_store.persist()

    def _active_slot(self) -> str:
        if self._binocular is None:
            return "single"
        return self.binocular.active_eye_slot()


# ---------------------------------------------------------------------------
# Generic serialisation helpers
# ---------------------------------------------------------------------------


def _serialize_result(result: object) -> object:
    """Recursively convert numpy / tuples / contours into JSON-friendly types.

    Mask arrays (any 2-D uint8 ndarray) are dropped — they are transient
    visualisation data and should not bloat the per-image annotation.
    """
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


def _serialize_overlays(states: dict | None) -> dict:
    """Convert a per-detector overlay map to JSON form (QColor -> hex string).

    ``states`` maps each detector id (plus ``manual``) to its overlay state,
    whose inner dicts map overlay key to style fields.
    """
    if not states:
        return {}
    out: dict = {}
    for det_id, state in states.items():
        det_out: dict = {}
        for key, fields in state.items():
            entry = dict(fields)
            color = entry.get("color")
            if isinstance(color, QColor):
                entry["color"] = color.name()
            det_out[key] = entry
        out[det_id] = det_out
    return out


def _deserialize_overlays(saved: dict) -> dict:
    """Convert a saved per-detector overlay map back to in-memory form.

    Hex colour strings become :class:`QColor`. Entries that are not the nested
    ``{detector_id: {overlay_key: fields}}`` shape yield empty states, so an
    older flat file simply falls back to the detectors' default overlays.
    """
    out: dict = {}
    for det_id, state in saved.items():
        if not isinstance(state, dict):
            continue
        det_out: dict = {}
        for key, fields in state.items():
            if not isinstance(fields, dict):
                continue
            entry = dict(fields)
            if isinstance(entry.get("color"), str):
                entry["color"] = QColor(entry["color"])
            det_out[key] = entry
        out[det_id] = det_out
    return out


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


def _extract_loaded_blob(
    blob: dict,
) -> tuple[dict[str, str | None], dict[str, dict | None], dict[str, dict | None]]:
    """Normalise an on-disk per-kind detection block to per-eye ``(ids, params, results)``.

    Each eye slot maps to its own ``{id, params, result}``.
    """
    ids: dict[str, str | None] = {}
    params: dict[str, dict | None] = {}
    results: dict[str, dict | None] = {}
    for slot in ("left", "right", "single"):
        entry = blob.get(slot)
        if not isinstance(entry, dict):
            continue
        ids[slot] = entry.get("id")
        params[slot] = entry.get("params") or None
        results[slot] = entry.get("result") or None
    return ids, params, results
