"""Run detector plugins in dependency order; cache results per image.

The orchestrator sits between the GUI side (the per-kind detector
cards) and the plugins discovered by :mod:`.plugin_loader`:

  - The controller registers one :class:`.plugin.DetectorPlugin` per
    kind via :meth:`set_enabled_detectors`. ``None`` means the kind is
    not run (Off or Manual).
  - On image change, the caller invokes :meth:`clear_cache`.
  - :meth:`run_one` runs a single kind, reusing whichever upstream
    results are cached.

Upstream wiring is implicit:

  - Glint plugins take ``pupil_center`` and ``pupil_radius`` as hidden
    keyword args. The orchestrator pops any user-supplied values and
    injects the cached pupil's centre + max-axis radius.
  - Limbus / eyelid plugins take the pupil centre (and optionally the
    pupil ellipse) as positional args after ``img``. The orchestrator
    passes them positionally regardless of the parameter name in the
    plugin's signature.

Two signals carry outcomes outward:

  - ``detector_ready(kind, result)`` after a successful call.
  - ``detector_failed(kind)`` when the call returned ``None`` or a
    required upstream result is missing.
"""

import logging
from collections.abc import Callable

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

from ..utils.project_settings import KINDS
from .plugin import DetectorPlugin

PostProcess = Callable[[dict], dict]

logger = logging.getLogger(__name__)


class DetectorOrchestrator(QObject):
    """Dependency-aware runner + per-image result cache for detector plugins."""

    detector_ready = pyqtSignal(str, dict)
    detector_failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        """Set up empty per-kind detector and result tables."""
        super().__init__(parent)
        self._enabled: dict[str, DetectorPlugin | None] = dict.fromkeys(KINDS, None)
        self._results: dict[str, dict | None] = dict.fromkeys(KINDS, None)

    # ----- configuration -----

    def set_enabled_detectors(self, per_kind: dict[str, DetectorPlugin | None]) -> None:
        """Replace the active detector set; wipe the cache for kinds that changed."""
        for kind in KINDS:
            new_det = per_kind.get(kind)
            if self._enabled[kind] is not new_det:
                self._enabled[kind] = new_det
                self._results[kind] = None

    def enabled_detector(self, kind: str) -> DetectorPlugin | None:
        """Return the detector enabled for ``kind``, or ``None``."""
        return self._enabled.get(kind)

    # ----- cache -----

    def cached_result(self, kind: str) -> dict | None:
        """Return the cached result for ``kind``, or ``None``."""
        return self._results.get(kind)

    def set_cached_result(self, kind: str, result: dict | None) -> None:
        """Store ``result`` as the cached result for ``kind``."""
        if kind not in self._results:
            raise ValueError(f"unknown kind {kind!r}")
        self._results[kind] = result

    def clear_cache(self) -> None:
        """Drop every cached detection result."""
        for kind in KINDS:
            self._results[kind] = None

    # ----- run paths -----

    def run_one(
        self,
        kind: str,
        image: np.ndarray,
        params: dict,
        post_process: PostProcess | None = None,
    ) -> None:
        """Re-run the detector enabled for ``kind`` with ``params``."""
        det = self._enabled.get(kind)
        if det is None:
            return
        self._run(kind, det, image, params, post_process=post_process)

    # ----- internals -----

    def _run(
        self,
        kind: str,
        det: DetectorPlugin,
        image: np.ndarray,
        params: dict,
        post_process: PostProcess | None = None,
    ) -> None:
        kwargs = dict(params)
        wired_args: list = []
        if det.kind == "glint":
            self._inject_pupil_kwargs_for_glint(kwargs)
        elif det.kind in {"limbus", "eyelid"}:
            wired_args = self._positional_pupil_for_limbus(det)
            if wired_args is None:
                self._results[kind] = None
                self.detector_failed.emit(kind)
                return
        try:
            result = det.function(image, *wired_args, **kwargs)
        except Exception:
            logger.exception("detector %r crashed", kind)
            self._results[kind] = None
            self.detector_failed.emit(kind)
            return
        if result is None:
            self._results[kind] = None
            self.detector_failed.emit(kind)
            return
        if post_process is not None:
            result = post_process(result)
        self._results[kind] = result
        self.detector_ready.emit(kind, result)

    def _inject_pupil_kwargs_for_glint(self, kwargs: dict) -> None:
        """Replace any user-supplied ``pupil_center`` / ``pupil_radius`` with cached values.

        Glint detectors take both as hidden settings with ``None``
        defaults. When pupil is cached we override; otherwise we leave
        the kwargs unset and let the detector fall back to whole-image
        search.
        """
        kwargs.pop("pupil_center", None)
        kwargs.pop("pupil_radius", None)
        pupil = self._results.get("pupil")
        if pupil is None:
            return
        ellipse = pupil.get("ellipse")
        if ellipse is None:
            return
        (_cx, _cy), (w, h), _angle = ellipse
        kwargs["pupil_center"] = pupil["center"]
        kwargs["pupil_radius"] = max(float(w), float(h)) / 2.0

    def _positional_pupil_for_limbus(self, det: DetectorPlugin) -> list | None:
        """Return the positional args limbus/eyelid detectors expect after ``img``.

        Every limbus detector takes the pupil centre as its 2nd
        positional, and any detector with a 3rd positional gets the
        pupil ellipse there. The parameter names in the plugin's
        signature (``seed_center``, ``pupil_ellipse``, etc.) are
        irrelevant — we match by position against ``det.wired_inputs``.
        """
        pupil = self._results.get("pupil")
        if pupil is None:
            return None
        center = pupil.get("center")
        if center is None:
            return None
        positional: list = [center]
        # ``wired_inputs`` includes the image as element 0; any further
        # entries are upstream positionals.
        if len(det.wired_inputs) >= 3:
            ellipse = pupil.get("ellipse")
            if ellipse is None:
                return None
            positional.append(ellipse)
        return positional


__all__ = ["DetectorOrchestrator"]
