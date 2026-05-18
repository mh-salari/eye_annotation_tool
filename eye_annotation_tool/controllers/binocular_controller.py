"""Binocular / monocular mode + per-image divider geometry.

The controller owns ``binocular_mode`` (the live flag) and the
divider-position lookup that turns a per-image override (or the
project default) into the canvas's divider line. It also coordinates
eye-switch — snapshotting the active eye's panel + orchestrator state
into :class:`PerEyeStateStore` before swapping to the other side.

Signals it consumes (wired in ``__init__``):

* ``annotation_controls.eye_changed`` — radio click.
* ``annotation_controls.binocular_toggled`` — checkbox flip.
* ``image_viewer.divider_x_norm_changed`` — divider drag.
"""

from collections.abc import Callable

from PyQt5.QtCore import QObject, pyqtSignal

from ..gui.annotation_controls import AnnotationControlPanel
from ..gui.image_viewer import ImageViewer
from ..policy import CliOverridePolicy
from ..state import PerEyeStateStore, ProjectStore
from .detection_controller import DetectionController


class BinocularController(QObject):
    """Owns the binocular flag, eye-switch state machine, and divider geometry."""

    annotation_modified = pyqtSignal(bool)

    def __init__(
        self,
        image_viewer: ImageViewer,
        annotation_controls: AnnotationControlPanel,
        per_eye_state: PerEyeStateStore,
        cli_policy: CliOverridePolicy,
        project_store: ProjectStore,
        detection_controller: DetectionController,
        *,
        current_image_path_fn: Callable[[], str | None],
        orchestrator: object,
        initial_binocular: bool,
        parent: QObject | None = None,
    ) -> None:
        """Wire dependencies and connect the annotation-controls + viewer signals.

        ``current_image_path_fn`` returns the active image's path, or
        ``None`` when no image is loaded. ``orchestrator`` is passed
        through to per-eye snapshot/restore. ``initial_binocular``
        seeds the flag before any project load mutates it.
        """
        super().__init__(parent)
        self.image_viewer = image_viewer
        self.annotation_controls = annotation_controls
        self.per_eye_state = per_eye_state
        self.cli_policy = cli_policy
        self.project_store = project_store
        self.detection_controller = detection_controller
        self._current_image_path_fn = current_image_path_fn
        self._orchestrator = orchestrator
        self._binocular: bool = bool(initial_binocular)

        annotation_controls.eye_changed.connect(self._on_eye_changed)
        annotation_controls.binocular_toggled.connect(self._on_binocular_toggled)
        image_viewer.divider_x_norm_changed.connect(self._on_divider_x_norm_changed)

    # ---------------------------------------------------------------------------
    # Mode flag + active-eye slot
    # ---------------------------------------------------------------------------

    @property
    def is_binocular(self) -> bool:
        """True when the active project is in binocular mode."""
        return self._binocular

    def active_eye_slot(self) -> str:
        """Return the per-eye cache slot for the currently active eye.

        ``"left"`` / ``"right"`` in binocular mode, ``"single"`` in
        monocular mode. Used as the dict key for the per-eye detection
        cache and the per-eye JSON detection block.
        """
        if not self._binocular:
            return "single"
        return self.image_viewer.current_eye

    def apply_mode(self, enabled: bool) -> None:
        """Sync the binocular flag to the dependent widgets (no persistence)."""
        self._binocular = bool(enabled)
        self.image_viewer.set_binocular_mode(self._binocular)
        self.annotation_controls.set_binocular(self._binocular)

    def _on_binocular_toggled(self, enabled: bool) -> None:
        """Handle the Binocular checkbox flipping; persist to the project file."""
        self.apply_mode(enabled)
        self.project_store.binocular_mode = enabled

    def _on_eye_changed(self, eye: str) -> None:
        """Switch the active eye and swap the per-eye panel + orchestrator state.

        The viewer keeps each eye's detection overlay / mask / ROI in
        its own slot so both halves' work stays visible across the
        switch — only the panel state and the orchestrator's dep cache
        (which only ever holds the active eye) get swapped here. Live
        plugins re-run against the new eye so the active half's
        overlay tracks the new panel values without waiting for slider
        drags.
        """
        if not self._binocular:
            return
        old_slot = self.active_eye_slot()
        self.per_eye_state.snapshot_orchestrator(old_slot, self._orchestrator)
        self.per_eye_state.snapshot_panel(old_slot, self.detection_controller.panel_for_target)
        self.image_viewer.switch_eye(eye)
        new_slot = self.active_eye_slot()
        self.per_eye_state.restore_panel(
            new_slot,
            self.detection_controller.panel_for_target,
            self.detection_controller.plugin_default_params,
        )
        self.per_eye_state.restore_orchestrator(new_slot, self._orchestrator)
        self.detection_controller.refresh_carry_checkboxes()
        self.detection_controller.refresh_manual_pupil_in_cache()
        self.detection_controller.refresh_live_plugin_results()
        self.detection_controller.refresh_panel_availability()

    # ---------------------------------------------------------------------------
    # Divider geometry
    # ---------------------------------------------------------------------------

    def divider_override_for_current_image(self) -> float | None:
        """Return the per-image divider override for the current image (or ``None``)."""
        image_path = self._current_image_path_fn()
        if image_path is None:
            return None
        return self.project_store.divider_override(image_path)

    def effective_divider_x_norm(self) -> float:
        """Return divider position for the current image (override or project default)."""
        override = self.divider_override_for_current_image()
        return self.project_store.divider_x_norm if override is None else override

    def _on_divider_x_norm_changed(self, value: float) -> None:
        """Persist a user-driven divider drag as a per-image override."""
        image_path = self._current_image_path_fn()
        if image_path is None:
            return
        self.project_store.set_divider_override(image_path, float(value))
        self.annotation_modified.emit(True)

    def apply_loaded_image_meta(self, *, binocular_mode: bool, divider_x_norm: float | None) -> None:
        """Apply binocular + divider metadata for a freshly loaded image.

        Called right after the image's annotation JSON has been parsed.
        The per-image divider override (or ``None`` to inherit the
        project default) is stashed for save round-trip, and the image
        viewer's divider position + binocular flag are updated so the
        canvas renders the correct geometry.

        The per-image binocular flag goes through
        :meth:`CliOverridePolicy.session_binocular` so the active CLI
        override policy is the only gate.
        """
        image_path = self._current_image_path_fn()
        if image_path is not None:
            self.project_store.set_divider_override(image_path, divider_x_norm)
        effective_binocular = self.cli_policy.session_binocular(binocular_mode)
        if effective_binocular != self._binocular:
            self.apply_mode(effective_binocular)
        self.image_viewer.set_divider_x_norm(self.effective_divider_x_norm())
