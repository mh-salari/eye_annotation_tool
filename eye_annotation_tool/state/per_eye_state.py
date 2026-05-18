"""Per-eye snapshots of detection results, panel params, and project defaults.

Binocular images carry one set of plugin state per eye; monocular
images use the ``"single"`` slot. The store owns three slot/target
tables and exposes the snapshot/restore dance with the orchestrator
and plugin panels behind named methods.

Orchestrator and panel coupling is kept thin: snapshot/restore take
the small slice of behaviour each method needs (a getter/setter for
the orchestrator, a lookup callable for the panel, a plugin-default
callable). The store itself imports no Qt symbols.
"""

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..auto_detectors.orchestrator import DetectorOrchestrator

CarryRoiSlot = str
Target = str


class _PluginPanel(Protocol):
    """Minimal slice of the plugin-panel API the store needs."""

    def current_params(self) -> dict:
        """Return a fresh dict of the panel's current parameter values."""

    def set_params(self, params: dict) -> None:
        """Apply ``params`` to the panel widgets without firing signals."""


class PerEyeStateStore:
    """Per-eye storage for orchestrator results, panel params, and project defaults.

    Three slots are tracked: ``"left"`` and ``"right"`` for binocular
    images, ``"single"`` for monocular. Each method that maps slot ->
    target writes ``None`` when there's no data yet so callers can
    distinguish "never touched" from "explicitly cleared" (both are
    semantically equivalent for the live panel restore, but the project
    file round-trip relies on the distinction).
    """

    SLOTS: tuple[CarryRoiSlot, ...] = ("left", "right", "single")

    def __init__(self, targets: Iterable[Target]) -> None:
        """Build the three slot/target tables for ``targets``.

        ``targets`` is the project's tuple of detector target names
        (e.g. ``("pupil", "glint", "limbus", "eyelid")``); the same
        tuple is used to seed every per-slot sub-dict so the store has
        one entry per (slot, target) from construction.
        """
        self._targets: tuple[Target, ...] = tuple(targets)
        self.detection_cache: dict[CarryRoiSlot, dict[Target, dict | None]] = {
            slot: dict.fromkeys(self._targets) for slot in self.SLOTS
        }
        self.panel_params: dict[CarryRoiSlot, dict[Target, dict | None]] = {
            slot: dict.fromkeys(self._targets) for slot in self.SLOTS
        }
        self.project_defaults: dict[Target, dict[CarryRoiSlot, dict | None]] = {
            target: dict.fromkeys(self.SLOTS) for target in self._targets
        }

    # ---------------------------------------------------------------------------
    # Orchestrator snapshot / restore
    # ---------------------------------------------------------------------------

    def snapshot_orchestrator(self, slot: CarryRoiSlot, orchestrator: "DetectorOrchestrator") -> None:
        """Copy the orchestrator's per-target results into ``slot``."""
        for target in self._targets:
            self.detection_cache[slot][target] = orchestrator.cached_result(target)

    def restore_orchestrator(self, slot: CarryRoiSlot, orchestrator: "DetectorOrchestrator") -> None:
        """Push the cached results for ``slot`` back into the orchestrator."""
        for target in self._targets:
            orchestrator.set_cached_result(target, self.detection_cache[slot][target])

    # ---------------------------------------------------------------------------
    # Panel-params snapshot / restore
    # ---------------------------------------------------------------------------

    def snapshot_panel(
        self,
        slot: CarryRoiSlot,
        panel_lookup_fn: Callable[[Target], _PluginPanel | None],
    ) -> None:
        """Mirror each live plugin panel's current params into ``slot``.

        ``panel_lookup_fn(target)`` returns the live panel widget (or
        ``None`` when no plugin is enabled for that target). Targets
        without a panel are left untouched in the mirror.
        """
        for target in self._targets:
            panel = panel_lookup_fn(target)
            if panel is None:
                continue
            self.panel_params[slot][target] = panel.current_params()

    def restore_panel(
        self,
        slot: CarryRoiSlot,
        panel_lookup_fn: Callable[[Target], _PluginPanel | None],
        plugin_default_fn: Callable[[Target], dict],
    ) -> None:
        """Push saved params back into each plugin panel.

        Priority order per (target, slot): per-image mirror →
        project-file defaults → plugin's ``default_params``. The last
        fallback keeps the panel snapped to clean defaults on a slot
        the user has never tuned and that has no project default,
        rather than letting the previous eye's tuning leak across.
        """
        for target in self._targets:
            panel = panel_lookup_fn(target)
            if panel is None:
                continue
            params = self.panel_params[slot].get(target)
            if params is None:
                params = self.project_defaults[target].get(slot)
            if params is None:
                params = plugin_default_fn(target)
            panel.set_params(params)

    # ---------------------------------------------------------------------------
    # Per-image lifecycle
    # ---------------------------------------------------------------------------

    def clear_all(self) -> None:
        """Wipe every per-eye cache slot (detection + panel params).

        Called on image change. Project defaults are NOT touched — they
        come from the project file and persist across images.
        """
        for slot in self.SLOTS:
            for target in self._targets:
                self.detection_cache[slot][target] = None
                self.panel_params[slot][target] = None

    # ---------------------------------------------------------------------------
    # Direct accessors (used by per-image save/load + run handlers)
    # ---------------------------------------------------------------------------

    def get_result(self, slot: CarryRoiSlot, target: Target) -> dict | None:
        """Return the cached detection result for ``(slot, target)`` (``None`` if absent)."""
        return self.detection_cache[slot][target]

    def set_result(self, slot: CarryRoiSlot, target: Target, value: dict | None) -> None:
        """Replace the cached detection result for ``(slot, target)``."""
        self.detection_cache[slot][target] = value

    def get_params(self, slot: CarryRoiSlot, target: Target) -> dict | None:
        """Return the mirrored panel params for ``(slot, target)`` (``None`` if untouched)."""
        return self.panel_params[slot].get(target)

    def set_params(self, slot: CarryRoiSlot, target: Target, value: dict | None) -> None:
        """Replace the mirrored panel params for ``(slot, target)``."""
        self.panel_params[slot][target] = value

    def get_project_default(self, target: Target, slot: CarryRoiSlot) -> dict | None:
        """Return the project-file default params for ``(target, slot)`` (``None`` if absent)."""
        return self.project_defaults[target].get(slot)

    def set_project_default(self, target: Target, slot: CarryRoiSlot, value: dict | None) -> None:
        """Replace the project-file default params for ``(target, slot)``."""
        self.project_defaults[target][slot] = value
