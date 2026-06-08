"""Per-eye snapshots of detection results, panel params, and project defaults.

Binocular images carry one set of plugin state per eye; monocular
images use the ``"single"`` slot. The store owns three slot/kind
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

EyeSlot = str
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
    kind writes ``None`` when there's no data yet so callers can
    distinguish "never touched" from "explicitly cleared" (both are
    semantically equivalent for the live panel restore, but the project
    file round-trip relies on the distinction).
    """

    SLOTS: tuple[EyeSlot, ...] = ("left", "right", "single")

    def __init__(self, kinds: Iterable[Target]) -> None:
        """Build the three slot/kind tables for ``kinds``.

        ``kinds`` is the project's tuple of detector kind names
        (e.g. ``("pupil", "glint", "limbus", "eyelid")``); the same
        tuple is used to seed every per-slot sub-dict so the store has
        one entry per (slot, kind) from construction.
        """
        self._targets: tuple[Target, ...] = tuple(kinds)
        self.detection_cache: dict[EyeSlot, dict[Target, dict | None]] = {
            slot: dict.fromkeys(self._targets) for slot in self.SLOTS
        }
        self.panel_params: dict[EyeSlot, dict[Target, dict | None]] = {
            slot: dict.fromkeys(self._targets) for slot in self.SLOTS
        }
        self.project_defaults: dict[Target, dict[EyeSlot, dict | None]] = {
            kind: dict.fromkeys(self.SLOTS) for kind in self._targets
        }

    # ---------------------------------------------------------------------------
    # Orchestrator snapshot / restore
    # ---------------------------------------------------------------------------

    def snapshot_orchestrator(self, slot: EyeSlot, orchestrator: "DetectorOrchestrator") -> None:
        """Copy the orchestrator's per-kind results into ``slot``."""
        for kind in self._targets:
            self.detection_cache[slot][kind] = orchestrator.cached_result(kind)

    def restore_orchestrator(self, slot: EyeSlot, orchestrator: "DetectorOrchestrator") -> None:
        """Push the cached results for ``slot`` back into the orchestrator."""
        for kind in self._targets:
            orchestrator.set_cached_result(kind, self.detection_cache[slot][kind])

    # ---------------------------------------------------------------------------
    # Panel-params snapshot / restore
    # ---------------------------------------------------------------------------

    def snapshot_panel(
        self,
        slot: EyeSlot,
        panel_lookup_fn: Callable[[Target], _PluginPanel | None],
    ) -> None:
        """Mirror each live plugin panel's current params into ``slot``.

        ``panel_lookup_fn(kind)`` returns the live panel widget (or
        ``None`` when no plugin is enabled for that kind). Targets
        without a panel are left untouched in the mirror.
        """
        for kind in self._targets:
            panel = panel_lookup_fn(kind)
            if panel is None:
                continue
            self.panel_params[slot][kind] = panel.current_params()

    def restore_panel(
        self,
        slot: EyeSlot,
        panel_lookup_fn: Callable[[Target], _PluginPanel | None],
        plugin_default_fn: Callable[[Target], dict],
    ) -> None:
        """Push saved params back into each plugin panel.

        Priority order per (kind, slot): per-image mirror →
        project-file defaults → plugin's ``default_params``. The last
        fallback keeps the panel snapped to clean defaults on a slot
        the user has never tuned and that has no project default,
        rather than letting the previous eye's tuning leak across.
        """
        for kind in self._targets:
            panel = panel_lookup_fn(kind)
            if panel is None:
                continue
            params = self.panel_params[slot].get(kind)
            if params is None:
                params = self.project_defaults[kind].get(slot)
            if params is None:
                params = plugin_default_fn(kind)
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
            for kind in self._targets:
                self.detection_cache[slot][kind] = None
                self.panel_params[slot][kind] = None

    # ---------------------------------------------------------------------------
    # Direct accessors (used by per-image save/load + run handlers)
    # ---------------------------------------------------------------------------

    def get_result(self, slot: EyeSlot, kind: Target) -> dict | None:
        """Return the cached detection result for ``(slot, kind)`` (``None`` if absent)."""
        return self.detection_cache[slot][kind]

    def set_result(self, slot: EyeSlot, kind: Target, value: dict | None) -> None:
        """Replace the cached detection result for ``(slot, kind)``."""
        self.detection_cache[slot][kind] = value

    def get_params(self, slot: EyeSlot, kind: Target) -> dict | None:
        """Return the mirrored panel params for ``(slot, kind)`` (``None`` if untouched)."""
        return self.panel_params[slot].get(kind)

    def set_params(self, slot: EyeSlot, kind: Target, value: dict | None) -> None:
        """Replace the mirrored panel params for ``(slot, kind)``."""
        self.panel_params[slot][kind] = value

    def get_project_default(self, kind: Target, slot: EyeSlot) -> dict | None:
        """Return the project-file default params for ``(kind, slot)`` (``None`` if absent)."""
        return self.project_defaults[kind].get(slot)

    def set_project_default(self, kind: Target, slot: EyeSlot, value: dict | None) -> None:
        """Replace the project-file default params for ``(kind, slot)``."""
        self.project_defaults[kind][slot] = value
