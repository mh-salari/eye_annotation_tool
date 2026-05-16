"""Run detector plugins in dependency order; cache results per image.

The orchestrator sits between the GUI (mode switcher + plugin panels) and
the plugins themselves:

  - Project settings name one plugin per target (or ``"disabled"``); the
    orchestrator holds the resolved instances via
    :meth:`set_enabled_plugins`.
  - On image change, the caller invokes :meth:`clear_cache`.
  - The "Run Auto Detect" button calls :meth:`run_all`, which walks the
    dependency graph by Kahn's algorithm on ``requires`` and runs each
    enabled, runnable plugin once. A plugin whose ``requires`` references
    a disabled target is skipped — never invoked — and ``plugin_failed``
    is emitted for it instead.
  - Live plugins re-run on debounced slider changes via :meth:`run_one`,
    which reuses whichever dependency results are currently cached.

Two signals carry outcomes outward:

  - ``plugin_ready(target, result)`` after a successful ``detect`` call.
    Listeners: the plugin panel (for UI feedback) and the image viewer
    (for overlay rendering).
  - ``plugin_failed(target)`` when ``detect`` returned ``None``, when a
    required dependency is disabled, or when a required dependency
    itself failed in this run. The cache entry for that target is set
    to ``None`` before the signal fires.
"""

from typing import cast

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

from .plugin_interface import DetectorPlugin, Target

TARGETS: tuple[Target, ...] = ("pupil", "glint", "limbus", "eyelid")


class DetectorOrchestrator(QObject):
    """Dependency-aware runner + per-image result cache for detector plugins."""

    # Payload: (target_name, result_dict). ``result_dict`` matches the shape
    # the plugin's ``serialize`` consumes.
    plugin_ready = pyqtSignal(str, dict)

    # Payload: target_name. Emitted when the plugin returned None, was
    # skipped because a required target is disabled, or a required
    # target's plugin failed in the same run.
    plugin_failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialise with no plugins enabled and an empty cache."""
        super().__init__(parent)
        self._enabled: dict[Target, DetectorPlugin | None] = dict.fromkeys(TARGETS, None)
        self._results: dict[Target, dict | None] = dict.fromkeys(TARGETS, None)

    # ----- configuration -----

    def set_enabled_plugins(self, per_target: dict[Target, DetectorPlugin | None]) -> None:
        """Replace the active plugin set and clear the cache.

        Cache is cleared so a stale result from a previously-enabled plugin
        cannot leak into a newly-enabled one's downstream invocation.
        """
        for target in TARGETS:
            self._enabled[target] = per_target.get(target)
        self.clear_cache()

    def enabled_plugin(self, target: Target) -> DetectorPlugin | None:
        """Return the plugin currently enabled for ``target`` (or ``None``)."""
        return self._enabled.get(target)

    # ----- cache -----

    def cached_result(self, target: Target) -> dict | None:
        """Return the last successful result cached for ``target`` on this image."""
        return self._results.get(target)

    def set_cached_result(self, target: Target, result: dict | None) -> None:
        """Inject a result into the cache without running a plugin.

        Used by the per-image restore path: when an annotation file already
        carries a previously saved detection, we deserialise it back into
        the cache so downstream plugins can read it via ``shared_results``
        and the viewer can render the overlay without re-running ``detect``.
        """
        if target not in self._results:
            raise ValueError(f"unknown target {target!r}")
        self._results[target] = result

    def clear_cache(self) -> None:
        """Forget every cached result. Call on image change."""
        for target in TARGETS:
            self._results[target] = None

    # ----- run paths -----

    def run_all(self, image: np.ndarray, per_target_params: dict[Target, dict]) -> None:
        """Run every enabled plugin on ``image`` in dependency order.

        ``per_target_params`` supplies the current panel values for each
        target. Missing entries fall back to the plugin's
        :meth:`~DetectorPlugin.default_params`.
        """
        self.clear_cache()
        active: dict[Target, DetectorPlugin] = {t: p for t, p in self._enabled.items() if p is not None}
        runnable_order = self._topological_runnable_order(active)
        runnable_targets = {p.target for p in runnable_order}
        for plugin in runnable_order:
            params = per_target_params.get(plugin.target, plugin.default_params())
            self._run_plugin(plugin, image, params)
        # Enabled plugins whose ``requires`` references a disabled target
        # cannot run at all this pass — surface the failure once.
        for target, plugin in active.items():
            if plugin.target not in runnable_targets:
                self._results[target] = None
                self.plugin_failed.emit(cast("str", target))

    def run_one(self, target: Target, image: np.ndarray, params: dict) -> None:
        """Re-run the plugin enabled for ``target`` with ``params``.

        Reuses whichever dependency results are currently cached. No-op if
        no plugin is enabled for ``target``. If any required target's
        cache is empty (dep disabled or its run failed), ``plugin_failed``
        is emitted and the cache for ``target`` is cleared.
        """
        plugin = self._enabled.get(target)
        if plugin is None:
            return
        self._run_plugin(plugin, image, params)

    # ----- internals -----

    def _run_plugin(self, plugin: DetectorPlugin, image: np.ndarray, params: dict) -> None:
        """Invoke a plugin's ``detect`` with its cached deps, update cache, emit signal."""
        shared: dict[str, dict] = {}
        for dep in plugin.requires:
            cached = self._results.get(dep)
            if cached is None:
                self._results[plugin.target] = None
                self.plugin_failed.emit(cast("str", plugin.target))
                return
            shared[dep] = cached
        result = plugin.detect(image, params, shared)
        if result is None:
            self._results[plugin.target] = None
            self.plugin_failed.emit(cast("str", plugin.target))
            return
        self._results[plugin.target] = result
        self.plugin_ready.emit(cast("str", plugin.target), result)

    @staticmethod
    def _topological_runnable_order(
        active: dict[Target, DetectorPlugin],
    ) -> list[DetectorPlugin]:
        """Order active plugins so each runs after every target it requires.

        Plugins whose ``requires`` references a disabled target are
        excluded — they cannot run. A dependency cycle among runnable
        plugins raises ``RuntimeError``.
        """
        runnable: dict[Target, DetectorPlugin] = {
            t: p for t, p in active.items() if all(dep in active for dep in p.requires)
        }
        unmet: dict[Target, set[Target]] = {t: set(p.requires) for t, p in runnable.items()}
        ready: list[Target] = sorted(t for t, deps in unmet.items() if not deps)
        ordered: list[DetectorPlugin] = []
        while ready:
            t = ready.pop(0)
            ordered.append(runnable[t])
            del unmet[t]
            for other_t, deps in unmet.items():
                if t in deps:
                    deps.remove(t)
                    if not deps:
                        ready.append(other_t)
            ready.sort()
        if unmet:
            cycle = ", ".join(sorted(unmet))
            raise RuntimeError(f"detector plugin dependency cycle among targets: {cycle}")
        return ordered
