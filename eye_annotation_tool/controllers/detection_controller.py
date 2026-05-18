"""Auto-Detect plugin lifecycle, run dispatch, signal wiring."""

from collections.abc import Callable

import numpy as np
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QMessageBox, QWidget

from ..auto_detectors import PluginManager
from ..auto_detectors.orchestrator import DetectorOrchestrator
from ..auto_detectors.plugin_interface import DetectorPlugin, Target
from ..gui.annotation_controls import AnnotationControlPanel
from ..gui.image_viewer import ImageViewer
from ..state import CarryRoiStore, PerEyeStateStore, ProjectStore
from ..utils.project_settings import CARRY_ROI_SLOTS, DETECTOR_TARGETS

AUTO_DETECT_DEBOUNCE_MS = 100

_PANEL_ROI_SETTER = "set_{target}_roi"


def _panel_roi_setter_name(target: str) -> str:
    """Return the panel-method name that pushes a new ROI for ``target``."""
    return _PANEL_ROI_SETTER.format(target=target)


def _panel_roi_param_key(target: str) -> str:
    """Return the params-dict key plugin panels use for their ``target`` ROI."""
    return f"{target}_roi"


class DetectionController(QObject):
    """Owns Auto-Detect plugin state, panel signal wiring, and the run pipeline.

    Holds the resolved-plugin dict, the debounce timer, and the
    last-manual-pupil signature; routes panel signals to the
    orchestrator; mediates project-file load/save of detection blocks;
    and handles the carry-ROI lifecycle for live tuning.

    Cross-cutting events the controller emits as signals (consumed by
    MainWindow):

    * :attr:`annotation_modified` — toggle the dirty flag.
    * :attr:`status_message` — show a transient status-bar message.
    * :attr:`detectors_changed` — fired after the project's detectors
      dict is mutated by a plugin selection so menus can refresh.
    """

    annotation_modified = pyqtSignal(bool)
    status_message = pyqtSignal(str, int)
    detectors_changed = pyqtSignal()

    def __init__(
        self,
        plugin_manager: PluginManager,
        orchestrator: DetectorOrchestrator,
        per_eye_state: PerEyeStateStore,
        carry_roi_state: CarryRoiStore,
        project_store: ProjectStore,
        image_viewer: ImageViewer,
        annotation_controls: AnnotationControlPanel,
        *,
        active_slot_fn: Callable[[], str],
        binocular_mode_fn: Callable[[], bool],
        effective_divider_fn: Callable[[], float],
        parent: QObject | None = None,
    ) -> None:
        """Wire the dependencies and start the debounce timer.

        ``active_slot_fn``, ``binocular_mode_fn``, ``effective_divider_fn``
        are the active-eye context hooks supplied by MainWindow until
        the BinocularController owns them in a later step.
        """
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.orchestrator = orchestrator
        self.per_eye_state = per_eye_state
        self.carry_roi_state = carry_roi_state
        self.project_store = project_store
        self.image_viewer = image_viewer
        self.annotation_controls = annotation_controls
        self._active_slot_fn = active_slot_fn
        self._binocular_mode_fn = binocular_mode_fn
        self._effective_divider_fn = effective_divider_fn

        self.enabled_plugins: dict[Target, DetectorPlugin] = {}
        self._pending_run_one: tuple[str, dict] | None = None
        self._last_manual_pupil_signature: tuple | None = None
        self._auto_detect_debounce = QTimer(self)
        self._auto_detect_debounce.setSingleShot(True)
        self._auto_detect_debounce.setInterval(AUTO_DETECT_DEBOUNCE_MS)
        self._auto_detect_debounce.timeout.connect(self._on_auto_detect_debounce_fired)
        self.orchestrator.plugin_ready.connect(self._on_plugin_ready)
        self.orchestrator.plugin_failed.connect(self._on_plugin_failed)
        self.image_viewer.target_roi_changed.connect(self._on_target_roi_changed)

    # ---------------------------------------------------------------------------
    # Tiny convenience accessors
    # ---------------------------------------------------------------------------

    def panel_for_target(self, target: Target) -> QWidget | None:
        """Look up the live Auto Detect panel for ``target`` (or ``None`` if disabled).

        Suitable as the ``panel_lookup_fn`` argument to
        :meth:`PerEyeStateStore.snapshot_panel` /
        :meth:`PerEyeStateStore.restore_panel`.
        """
        plugin = self.enabled_plugins.get(target)
        if plugin is None:
            return None
        return self.annotation_controls.auto_detect_panel(plugin.name)

    def plugin_default_params(self, target: Target) -> dict:
        """Return the active plugin's ``default_params`` for ``target`` (or ``{}`` if disabled)."""
        plugin = self.enabled_plugins.get(target)
        if plugin is None:
            return {}
        return plugin.default_params()

    # ---------------------------------------------------------------------------
    # Plugin resolution + panel rebuild
    # ---------------------------------------------------------------------------

    def apply_enabled_plugins(
        self,
        detectors_settings: dict,
        preserved_params: dict | None = None,
    ) -> None:
        """Resolve enabled plugins from project settings and (re)build the Auto Detect stack.

        ``detectors_settings`` is the ``"detectors"`` block:
        ``{target: {"plugin": name, "params": {...}}, ...}``. Targets
        whose plugin is ``"disabled"`` are skipped. Unknown plugin names
        raise ``RuntimeError`` — silent skipping would hide typos.

        ``preserved_params`` lets a caller override the project-file
        params for specific targets, used by :meth:`select_plugin_for_target`
        to keep the in-memory slider state of unchanged targets when
        the user toggles one detector via the menu.
        """
        preserved_params = preserved_params or {}
        self.enabled_plugins = {}
        panels: list[tuple[str, QWidget]] = []
        for target in DETECTOR_TARGETS:
            entry = detectors_settings.get(target) or {}
            plugin_name = entry.get("plugin", "disabled")
            if plugin_name == "disabled":
                self.image_viewer.clear_active_plugin(target)
                continue
            plugin = self.plugin_manager.get(plugin_name)
            if plugin is None:
                raise RuntimeError(
                    f"project settings reference unknown plugin {plugin_name!r} for target {target!r}; "
                    f"available: {sorted(self.plugin_manager.all())}",
                )
            if plugin.target != target:
                raise RuntimeError(
                    f"plugin {plugin_name!r} targets {plugin.target!r} but is configured for {target!r}",
                )
            self.enabled_plugins[target] = plugin
            self.image_viewer.set_active_plugin(target, plugin)
            panel = plugin.make_panel(None)
            params_by_slot = entry.get("params") or {}
            for slot in CARRY_ROI_SLOTS:
                slot_params = params_by_slot.get(slot) if isinstance(params_by_slot, dict) else None
                self.per_eye_state.set_project_default(
                    target,
                    slot,
                    dict(slot_params) if isinstance(slot_params, dict) else None,
                )
            active_slot = self._active_slot_fn()
            initial_params = (
                preserved_params.get(target) or self.per_eye_state.get_project_default(target, active_slot) or {}
            )
            panel.set_params(initial_params)
            panel.params_changed.connect(
                lambda params, name=plugin.name, target_=plugin.target: self._on_plugin_params_changed(
                    name,
                    target_,
                    params,
                ),
            )
            if hasattr(panel, "roi_edit_requested"):
                panel.roi_edit_requested.connect(
                    lambda checked, target_=plugin.target: self._on_panel_roi_edit_requested(target_, checked),
                )
            if hasattr(panel, "clear_roi_requested"):
                panel.clear_roi_requested.connect(
                    lambda target_=plugin.target: self._on_panel_clear_roi_requested(target_),
                )
            if hasattr(panel, "show_mask_toggled"):
                panel.show_mask_toggled.connect(
                    lambda on, target_=plugin.target: self._on_panel_show_mask_toggled(target_, on),
                )
            if hasattr(panel, "show_ellipse_toggled"):
                panel.show_ellipse_toggled.connect(
                    lambda on, plugin_=plugin: self._on_panel_show_ellipse_toggled(plugin_, on),
                )
                # Plugin instances outlive their panels; sync the persistent
                # render flag to the freshly built checkbox state so a
                # disable / re-enable cycle does not leave the plugin showing
                # an outline the new panel claims is off.
                if hasattr(plugin, "set_show_ellipse") and hasattr(panel, "show_ellipse_check"):
                    plugin.set_show_ellipse(panel.show_ellipse_check.isChecked())
            if hasattr(panel, "detect_requested"):
                panel.detect_requested.connect(
                    lambda name=plugin.name, target_=plugin.target: self._on_panel_detect_requested(name, target_),
                )
            self.carry_roi_state.load_from_project_block(target, entry.get("carry_roi") or {})
            if hasattr(panel, "set_carry_roi_enabled"):
                panel.set_carry_roi_enabled(self.carry_roi_state.is_enabled(target, active_slot))
            if hasattr(panel, "carry_roi_toggled"):
                panel.carry_roi_toggled.connect(
                    lambda checked, target_=plugin.target: self._on_carry_roi_toggled(target_, checked),
                )
            if hasattr(panel, "override_roi_requested"):
                panel.override_roi_requested.connect(
                    lambda target_=plugin.target: self._on_override_roi_requested(target_),
                )
            panels.append((plugin.name, panel))
        self.annotation_controls.set_auto_detect_panels(panels)
        self.orchestrator.set_enabled_plugins(dict(self.enabled_plugins))
        self.sync_manual_for_auto_targets()
        self._last_manual_pupil_signature = self._manual_pupil_signature()
        self.refresh_manual_pupil_in_cache()
        self.refresh_panel_availability()

    def sync_manual_for_auto_targets(self) -> None:
        """Mirror per-target detector ownership into the Manual panel + viewer.

        For each target with an enabled auto detector the matching
        Manual-panel row is hidden entirely; the viewer is told to
        suppress that target's manual painting and click-add. If the
        currently selected Manual row just got hidden, selection jumps
        to the first still-visible row so canvas clicks don't fall
        through to an invisible target.
        """
        target_rows = (
            ("pupil", self.annotation_controls.pupil_group, "pupil"),
            ("limbus", self.annotation_controls.limbus_group, "limbus"),
            ("eyelid", self.annotation_controls.eyelid_group, "eyelid_contour"),
            ("glint", self.annotation_controls.glint_group, "glint"),
        )
        auto_targets: set[str] = set()
        first_visible: tuple[object, str] | None = None
        selected_hidden = False
        for plugin_target, group, annotation in target_rows:
            has_auto = plugin_target in self.enabled_plugins
            group.setVisible(not has_auto)
            if has_auto:
                auto_targets.add(plugin_target)
                if group.is_checked():
                    selected_hidden = True
            elif first_visible is None:
                first_visible = (group, annotation)
        self.image_viewer.set_auto_managed_targets(auto_targets)
        if selected_hidden and first_visible is not None:
            group, annotation = first_visible
            group.set_checked(True)
            self.image_viewer.set_current_annotation(annotation)

    # ---------------------------------------------------------------------------
    # Auto Detectors menu actions
    # ---------------------------------------------------------------------------

    def current_plugin_for_target(self, target: Target) -> str:
        """Return the slug of the plugin currently chosen for ``target`` (or ``"disabled"``)."""
        return self.project_store.project.get("detectors", {}).get(target, {}).get("plugin", "disabled")

    def select_plugin_for_target(self, target: Target, plugin_name: str) -> None:
        """Set ``target``'s plugin to ``plugin_name`` and rebuild the Auto Detect panels.

        Switching plugins resets the saved params for that target to the
        new plugin's :meth:`~DetectorPlugin.default_params`. Switching
        to the same plugin is a no-op.
        """
        detectors = self.project_store.project.setdefault("detectors", {})
        current = detectors.get(target, {}).get("plugin", "disabled")
        if current == plugin_name:
            return
        if plugin_name == "disabled":
            detectors[target] = {"plugin": "disabled", "params": {}}
        else:
            plugin = self.plugin_manager.get(plugin_name)
            if plugin is None or plugin.target != target:
                return
            detectors[target] = {"plugin": plugin_name, "params": plugin.default_params()}
        self.project_store.persist()
        self.image_viewer.clear_detection_overlay(target)
        self.image_viewer.clear_target_roi(target)
        self.image_viewer.clear_target_mask(target)
        if plugin_name != "disabled":
            self.image_viewer.clear_manual_for_target(target)
        preserved: dict[Target, dict] = {}
        for t, plugin in self.enabled_plugins.items():
            if t == target:
                continue
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is not None:
                preserved[t] = panel.current_params()
        self.apply_enabled_plugins(detectors, preserved_params=preserved)
        self.detectors_changed.emit()
        self.refresh_live_plugins_all_eyes()

    def save_current_settings_as_project_defaults(self, parent_widget: QWidget) -> None:
        """Snapshot every enabled plugin's current panel params into project defaults.

        ``parent_widget`` parents the confirmation dialog (passed in so
        the controller stays unaware of MainWindow).
        """
        if not self.enabled_plugins:
            QMessageBox.information(
                parent_widget,
                "No Detectors Enabled",
                "Enable at least one detector via the Auto Detectors menu before saving project defaults.",
            )
            return
        reply = QMessageBox.question(
            parent_widget,
            "Save Project Defaults?",
            "Replace this project's saved detector defaults with the current Auto Detect panel values?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        active_slot = self._active_slot_fn()
        self.per_eye_state.snapshot_panel(active_slot, self.panel_for_target)
        detectors = self.project_store.project.setdefault("detectors", {})
        for target, plugin in self.enabled_plugins.items():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            for slot in CARRY_ROI_SLOTS:
                self.carry_roi_state.set_value(
                    target,
                    slot,
                    self.image_viewer.get_target_roi(target, eye_slot=slot),
                )
            params_by_slot: dict[str, dict | None] = {}
            for slot in CARRY_ROI_SLOTS:
                per_slot = self.per_eye_state.get_params(slot, target)
                if per_slot is None:
                    params_by_slot[slot] = None
                    continue
                cleaned = dict(per_slot)
                cleaned.pop(_panel_roi_param_key(target), None)
                params_by_slot[slot] = cleaned
                self.per_eye_state.set_project_default(target, slot, dict(cleaned))
            detectors[target] = {
                "plugin": plugin.name,
                "params": params_by_slot,
                "carry_roi": self.carry_roi_state.to_project_block(target),
            }
        self.project_store.persist()
        self.refresh_carry_checkboxes()
        self.status_message.emit("Project defaults saved.", 3000)

    # ---------------------------------------------------------------------------
    # Per-image detection block round-trip
    # ---------------------------------------------------------------------------

    def collect_detections_for_save(self) -> dict:
        """Walk every enabled plugin and build the per-image ``detections`` dict.

        Monocular images save flat: ``{plugin_name: {params, result}}``.
        Binocular images save nested per eye: ``{plugin_name: {left:
        {params, result}, right: {params, result}}}``.
        """
        active_slot = self._active_slot_fn()
        self.per_eye_state.snapshot_orchestrator(active_slot, self.orchestrator)
        self.per_eye_state.snapshot_panel(active_slot, self.panel_for_target)
        binocular = self._binocular_mode_fn()
        out: dict = {}
        for target, plugin in self.enabled_plugins.items():
            if binocular:
                per_eye_block: dict = {}
                for slot in ("left", "right"):
                    result = self.per_eye_state.get_result(slot, target)
                    if result is None:
                        continue
                    params = self.per_eye_state.get_params(slot, target) or plugin.default_params()
                    per_eye_block[slot] = {
                        "params": params,
                        "result": plugin.serialize(result),
                    }
                if per_eye_block:
                    out[plugin.name] = per_eye_block
            else:
                result = self.per_eye_state.get_result("single", target)
                if result is None:
                    continue
                params = self.per_eye_state.get_params("single", target) or plugin.default_params()
                out[plugin.name] = {
                    "params": params,
                    "result": plugin.serialize(result),
                }
        return out

    def apply_loaded_detections(self, detections: dict) -> None:
        """Restore per-image detection blocks from a loaded annotation file.

        Two on-disk shapes are accepted: monocular files carry a flat
        ``{params, result}`` per plugin; binocular files carry a nested
        ``{left: {...}, right: {...}}`` per plugin. Saved per-image
        ROIs win over carry-over rectangles; the carry-over only fills
        slots the JSON didn't populate.
        """
        self.image_viewer.pause_updates()
        try:
            active_slot = self._active_slot_fn()
            for plugin_name, blob in detections.items():
                plugin = self.plugin_manager.get(plugin_name)
                if plugin is None:
                    continue
                if self.enabled_plugins.get(plugin.target) is not plugin:
                    continue
                per_eye_params, per_eye_results = self._extract_loaded_plugin_blob(blob, plugin)
                for slot, params in per_eye_params.items():
                    if params is None:
                        continue
                    self.per_eye_state.set_params(slot, plugin.target, dict(params))
                    saved_roi = params.get(_panel_roi_param_key(plugin.target))
                    self.image_viewer.set_target_roi(
                        plugin.target,
                        tuple(saved_roi) if saved_roi is not None else None,
                        eye_slot=slot,
                    )
                for slot, result in per_eye_results.items():
                    self.per_eye_state.set_result(slot, plugin.target, result)
                    if result is not None:
                        self.image_viewer.set_detection_overlay(plugin.target, result, eye_slot=slot)
                active_params = per_eye_params.get(active_slot)
                active_result = per_eye_results.get(active_slot)
                panel = self.annotation_controls.auto_detect_panel(plugin.name)
                if panel is not None and active_params is not None:
                    panel.set_params(active_params)
                if active_result is not None:
                    self.orchestrator.set_cached_result(plugin.target, active_result)
            self.per_eye_state.restore_panel(active_slot, self.panel_for_target, self.plugin_default_params)
            self.apply_carry_over_rois()
            self.refresh_carry_checkboxes()
            self._last_manual_pupil_signature = self._manual_pupil_signature()
            self.refresh_manual_pupil_in_cache()
            self.refresh_live_plugins_all_eyes()
            self.refresh_panel_availability()
        finally:
            self.image_viewer.resume_updates()

    @staticmethod
    def _extract_loaded_plugin_blob(
        blob: dict,
        plugin: DetectorPlugin,
    ) -> tuple[dict[str, dict | None], dict[str, dict | None]]:
        """Normalise an on-disk plugin block to per-eye ``(params, results)`` maps."""
        if "params" in blob or "result" in blob:
            params = blob.get("params") or None
            result_blob = blob.get("result")
            result = plugin.deserialize(result_blob) if result_blob else None
            return {"single": params}, {"single": result}
        per_eye_params: dict[str, dict | None] = {}
        per_eye_results: dict[str, dict | None] = {}
        for slot in ("left", "right"):
            entry = blob.get(slot)
            if not isinstance(entry, dict):
                continue
            per_eye_params[slot] = entry.get("params") or None
            result_blob = entry.get("result")
            per_eye_results[slot] = plugin.deserialize(result_blob) if result_blob else None
        return per_eye_params, per_eye_results

    # ---------------------------------------------------------------------------
    # Manual-pupil bridge: synthetic pupil result for downstream auto plugins
    # ---------------------------------------------------------------------------

    def _manual_pupil_signature(self) -> tuple | None:
        """Hashable identity of the current eye's manual pupil ellipse."""
        pupil_ellipse = self.image_viewer.pupil_ellipse
        if pupil_ellipse is None:
            return None
        center, size, angle = pupil_ellipse
        return (center.x(), center.y(), size.width(), size.height(), angle)

    def _build_synthetic_pupil_from_manual(self) -> dict | None:
        """Build a pupil-plugin-shaped result from the current manual pupil ellipse, or None."""
        pupil_ellipse = self.image_viewer.pupil_ellipse
        if pupil_ellipse is None:
            return None
        center, size, angle = pupil_ellipse
        cx, cy = float(center.x()), float(center.y())
        return {
            "center": [cx, cy],
            "ellipse": {
                "center": [cx, cy],
                "size": [float(size.width()), float(size.height())],
                "angle": float(angle),
            },
        }

    def refresh_manual_pupil_in_cache(self) -> None:
        """Mirror the current manual pupil ellipse into the orchestrator cache.

        Lets glint / limbus auto detectors consume a manually fitted pupil
        through the same ``shared_results["pupil"]`` path they use for an
        auto pupil result. No-op when an auto pupil plugin is enabled —
        that plugin owns the cache slot.
        """
        if "pupil" in self.enabled_plugins:
            return
        self.orchestrator.set_cached_result("pupil", self._build_synthetic_pupil_from_manual())

    def on_manual_annotation_changed(self) -> bool:
        """React to a manual-annotation edit; return True if downstream plugins re-ran.

        The MainWindow's annotation-changed signal handler calls this
        after marking the project dirty. When pupil ownership lies on
        the manual side and the ellipse identity changed, the
        synthetic pupil is republished and live downstream plugins
        re-run so their overlay tracks the new pupil immediately.
        """
        if "pupil" in self.enabled_plugins:
            return False
        new_sig = self._manual_pupil_signature()
        if new_sig == self._last_manual_pupil_signature:
            return False
        self._last_manual_pupil_signature = new_sig
        self.refresh_manual_pupil_in_cache()
        self.refresh_live_plugin_results()
        self.refresh_panel_availability()
        return True

    # ---------------------------------------------------------------------------
    # Refresh helpers
    # ---------------------------------------------------------------------------

    def refresh_panel_availability(self) -> None:
        """Disable each Auto Detect panel whose ``requires`` are unmet."""
        for plugin in self.enabled_plugins.values():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            deps_met = all(self.orchestrator.cached_result(dep) is not None for dep in plugin.requires)
            panel.setEnabled(deps_met)

    def refresh_live_plugin_results(self) -> None:
        """Re-run every enabled live plugin on the active eye, in dep order."""
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return
        for target in DETECTOR_TARGETS:
            plugin = self.enabled_plugins.get(target)
            if plugin is None or not plugin.live:
                continue
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            self._run_plugin_for_active_eye(plugin, panel.current_params())

    def refresh_live_plugins_all_eyes(self) -> None:
        """Run live plugins for the active eye only.

        The non-active eye is intentionally skipped — programmatically
        running live plugins on a slot the user never visited would
        populate that slot's detection cache with default-params output
        and then autosave would persist those defaults to disk, looking
        like the user tuned that eye when they hadn't. Switching the
        active eye (via the radio) triggers the live run for the new
        side, so both eyes are still covered with one user click each.
        """
        self.refresh_live_plugin_results()

    def cancel_active_roi_edit(self) -> None:
        """Drop the active ROI drag-edit state on the canvas and untoggle every panel button.

        Used when leaving Auto Detect mode so the canvas stops treating
        clicks as ROI edits and so the panel button doesn't stay stuck
        in its checked state when the user comes back.
        """
        self.image_viewer.set_active_roi_target(None)
        for plugin in self.enabled_plugins.values():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            button = getattr(panel, "roi_button", None) if panel is not None else None
            if button is not None and button.isChecked():
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)

    # ---------------------------------------------------------------------------
    # Run pipeline + binocular crop helpers
    # ---------------------------------------------------------------------------

    def _active_eye_crop_bounds(self) -> tuple[int, int, int, int] | None:
        """Return ``(dx, dy, dw, dh)`` for the active eye's half, or ``None`` (no crop)."""
        if not self._binocular_mode_fn():
            return None
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return None
        full_h, full_w = image.shape[:2]
        divider_x = round(self._effective_divider_fn() * full_w)
        divider_x = max(1, min(full_w - 1, divider_x))
        if self.image_viewer.current_eye == "left":
            return (0, 0, divider_x, full_h)
        return (divider_x, 0, full_w - divider_x, full_h)

    @staticmethod
    def _intersect_roi_with_crop(
        roi: tuple | None,
        crop: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int] | None:
        """Translate a full-image ROI into crop coords, or return None if no overlap."""
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

    @staticmethod
    def _embed_mask(mask: np.ndarray, dx: int, dy: int, full_shape: tuple) -> np.ndarray:
        """Paste a crop-sized mask into a full-image-sized zeros array at ``(dx, dy)``."""
        full_h, full_w = full_shape[:2]
        embedded = np.zeros((full_h, full_w), dtype=mask.dtype)
        mh, mw = mask.shape[:2]
        embedded[dy : dy + mh, dx : dx + mw] = mask
        return embedded

    def _run_plugin_for_active_eye(self, plugin: DetectorPlugin, params: dict) -> None:
        """Dispatch a plugin run, cropping to the active eye half for pupil plugins in binocular mode."""
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return
        bounds = self._active_eye_crop_bounds() if plugin.target == "pupil" else None
        if bounds is None:
            self.orchestrator.run_one(plugin.target, image, params)
            return
        dx, dy, dw, dh = bounds
        cropped = image[dy : dy + dh, dx : dx + dw]
        translated_params = dict(params)
        roi_key = _panel_roi_param_key(plugin.target)
        if roi_key in translated_params:
            translated_params[roi_key] = self._intersect_roi_with_crop(translated_params.get(roi_key), bounds)
        full_shape = image.shape

        def post_process(result: dict) -> dict:
            translated = plugin.translate_for_crop(result, dx, dy)
            mask = translated.get("mask")
            if mask is not None:
                translated["mask"] = self._embed_mask(mask, dx, dy, full_shape)
            return translated

        self.orchestrator.run_one(plugin.target, cropped, translated_params, post_process=post_process)

    # ---------------------------------------------------------------------------
    # Panel signal handlers
    # ---------------------------------------------------------------------------

    def _on_plugin_params_changed(self, plugin_name: str, target: Target, params: dict) -> None:
        """Route a panel parameter change by the plugin's ``live`` flag.

        Live plugins re-run via the debounce path so slider drags collapse
        to a single ``run_one`` call. Non-live plugins (e.g. Daugman
        limbus) drop their cached result + overlay + mask immediately
        so the user is never looking at a stale visualisation; the next
        detection only happens when the user clicks Detect.
        """
        self.annotation_modified.emit(True)
        plugin = self.plugin_manager.get(plugin_name)
        if plugin is None:
            return
        if plugin.live:
            self._pending_run_one = (plugin_name, dict(params))
            self._auto_detect_debounce.start()
            return
        active_slot = self._active_slot_fn()
        self.orchestrator.set_cached_result(target, None)
        self.per_eye_state.set_result(active_slot, target, None)
        self.image_viewer.clear_detection_overlay(target, eye_slot=active_slot)
        self.image_viewer.clear_target_mask(target, eye_slot=active_slot)

    def _on_auto_detect_debounce_fired(self) -> None:
        """Dispatch the buffered run to the active-eye-aware run helper."""
        pending = self._pending_run_one
        self._pending_run_one = None
        if pending is None:
            return
        plugin_name, params = pending
        plugin = self.plugin_manager.get(plugin_name)
        if plugin is None or self.enabled_plugins.get(plugin.target) is not plugin:
            return
        self._run_plugin_for_active_eye(plugin, params)

    def _on_plugin_ready(self, target: str, result: dict) -> None:
        """Render the new detection result for the active eye + mark modified."""
        active_slot = self._active_slot_fn()
        self.image_viewer.set_target_mask(target, result.get("mask"), eye_slot=active_slot)
        self.image_viewer.set_detection_overlay(target, result, eye_slot=active_slot)
        self.per_eye_state.set_result(active_slot, target, result)
        self.annotation_modified.emit(True)
        self.refresh_panel_availability()

    def _on_plugin_failed(self, target: str) -> None:
        """Clear the active eye's overlay + mask for ``target`` and report in the status bar."""
        active_slot = self._active_slot_fn()
        self.image_viewer.clear_detection_overlay(target, eye_slot=active_slot)
        self.image_viewer.clear_target_mask(target, eye_slot=active_slot)
        self.per_eye_state.set_result(active_slot, target, None)
        self.status_message.emit(f"Auto Detect: {target} failed at current parameters.", 5000)
        self.refresh_panel_availability()

    def clear_all_auto_detect(self) -> None:
        """Reset every Auto Detect plugin panel + orchestrator + per-eye cache + viewer state."""
        self._auto_detect_debounce.stop()
        self._pending_run_one = None
        for target, plugin in self.enabled_plugins.items():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            panel.set_params(plugin.default_params())
            self.orchestrator.set_cached_result(target, None)
            self.image_viewer.clear_detection_overlay(target)
            self.image_viewer.clear_target_mask(target)
            self.image_viewer.clear_target_roi(target)
        self.per_eye_state.clear_all()
        self.image_viewer.set_active_roi_target(None)
        self.annotation_modified.emit(True)

    def _on_panel_detect_requested(self, plugin_name: str, target: Target) -> None:
        """Run a non-live plugin once on the active eye."""
        plugin = self.enabled_plugins.get(target)
        if plugin is None or plugin.name != plugin_name:
            return
        panel = self.annotation_controls.auto_detect_panel(plugin_name)
        if panel is None:
            return
        self._run_plugin_for_active_eye(plugin, panel.current_params())

    def _on_panel_show_mask_toggled(self, target: str, on: bool) -> None:
        """Forward the panel's "Show mask" toggle to the viewer."""
        self.image_viewer.set_show_target_mask(target, on)

    def _on_panel_show_ellipse_toggled(self, plugin: object, on: bool) -> None:
        """Flip the plugin's fitted-ellipse render flag and repaint the canvas."""
        setter = getattr(plugin, "set_show_ellipse", None)
        if setter is None:
            return
        setter(on)
        self.image_viewer.update_image()

    def _on_panel_roi_edit_requested(self, target: str, active: bool) -> None:
        """Enter (or leave) drag-edit mode for ``target``'s ROI on the canvas."""
        self.image_viewer.set_active_roi_target(target if active else None)

    def _on_panel_clear_roi_requested(self, target: str) -> None:
        """Drop ``target``'s ROI everywhere: viewer store, panel params, plus re-run."""
        self.image_viewer.set_target_roi(target, None)
        plugin = self.enabled_plugins.get(target)
        if plugin is None:
            return
        panel = self.annotation_controls.auto_detect_panel(plugin.name)
        setter = getattr(panel, _panel_roi_setter_name(target), None) if panel is not None else None
        if setter is not None:
            setter(None)

    def _on_target_roi_changed(self, target: str, roi: tuple | None) -> None:
        """Push a canvas-edited ROI back into the panel; disable Carry on edit.

        A canvas edit means the user is tuning THIS image's ROI
        specifically, so Carry auto-disables for the active eye when a
        non-empty rectangle lands. The other eye's carry stays untouched.
        """
        plugin = self.enabled_plugins.get(target)
        if plugin is None:
            return
        panel = self.annotation_controls.auto_detect_panel(plugin.name)
        setter = getattr(panel, _panel_roi_setter_name(target), None) if panel is not None else None
        if setter is not None:
            setter(roi)
        active_slot = self._active_slot_fn()
        if roi is not None and self.carry_roi_state.is_enabled(target, active_slot):
            self.carry_roi_state.set_enabled(target, active_slot, False)
            if panel is not None and hasattr(panel, "set_carry_roi_enabled"):
                panel.set_carry_roi_enabled(False)
            self._persist_carry_roi(target)

    # ---------------------------------------------------------------------------
    # Carry-over ROI handlers
    # ---------------------------------------------------------------------------

    def _on_carry_roi_toggled(self, target: Target, enabled: bool) -> None:
        """Persist the active eye's carry-over enable flag for ``target``."""
        active_slot = self._active_slot_fn()
        self.carry_roi_state.set_enabled(target, active_slot, enabled)
        if enabled:
            current_roi = self.image_viewer.get_target_roi(target)
            if current_roi is not None:
                self.carry_roi_state.set_value(target, active_slot, current_roi)
        self._persist_carry_roi(target)
        self.refresh_carry_checkboxes()

    def _on_override_roi_requested(self, target: Target) -> None:
        """Push the stored carry-over rectangle into the active eye's panel + viewer."""
        active_slot = self._active_slot_fn()
        carry_value = self.carry_roi_state.get_value(target, active_slot)
        if carry_value is None:
            return
        plugin = self.enabled_plugins.get(target)
        if plugin is None:
            return
        panel = self.annotation_controls.auto_detect_panel(plugin.name)
        setter = getattr(panel, _panel_roi_setter_name(target), None) if panel is not None else None
        if setter is not None:
            setter(tuple(carry_value))
        self.image_viewer.set_target_roi(target, tuple(carry_value), eye_slot=active_slot)
        self.refresh_carry_checkboxes()

    def _persist_carry_roi(self, target: Target) -> None:
        """Write the per-eye carry-over enable flags + values for ``target`` to the project file."""
        detectors = self.project_store.project.setdefault("detectors", {})
        entry = detectors.setdefault(target, {"plugin": "disabled", "params": {}})
        entry["carry_roi"] = self.carry_roi_state.to_project_block(target)
        self.project_store.persist()

    def refresh_carry_checkboxes(self) -> None:
        """Sync each panel's Carry checkbox + Override button to the active eye's state."""
        active_slot = self._active_slot_fn()
        for target, plugin in self.enabled_plugins.items():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            if hasattr(panel, "set_carry_roi_enabled"):
                viewer_roi = self.image_viewer.get_target_roi(target, eye_slot=active_slot)
                panel.set_carry_roi_enabled(self.carry_roi_state.checkbox_state(target, active_slot, viewer_roi))
            if hasattr(panel, "set_override_button_enabled"):
                panel.set_override_button_enabled(self.carry_roi_state.get_value(target, active_slot) is not None)

    def apply_carry_over_rois(self) -> None:
        """Inject the carry-over rectangle into every (target, eye) without a saved ROI.

        Called from :meth:`apply_loaded_detections` so saved per-image
        ROIs take precedence — the carry-over only fills the slots that
        the JSON didn't populate. The viewer's per-eye ROI store and
        the active eye's live panel are both updated.
        """
        active_slot = self._active_slot_fn()
        slots = ("left", "right") if self._binocular_mode_fn() else ("single",)
        for target, plugin in self.enabled_plugins.items():
            roi_key = _panel_roi_param_key(target)
            already_filled = [
                slot
                for slot in slots
                if (params := self.per_eye_state.get_params(slot, target)) is not None
                and params.get(roi_key) is not None
            ]
            for slot, carry_value in self.carry_roi_state.pending_slots_for_apply(target, slots, already_filled):
                params = self.per_eye_state.get_params(slot, target)
                if params is None:
                    self.per_eye_state.set_params(slot, target, {roi_key: list(carry_value)})
                else:
                    params[roi_key] = list(carry_value)
                self.image_viewer.set_target_roi(target, tuple(carry_value), eye_slot=slot)
                if slot == active_slot:
                    panel = self.annotation_controls.auto_detect_panel(plugin.name)
                    panel_setter = getattr(panel, _panel_roi_setter_name(target), None) if panel is not None else None
                    if panel_setter is not None:
                        panel_setter(tuple(carry_value))
