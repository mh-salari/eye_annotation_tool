"""Run cheshm detector functions in dependency order; cache results per image.

The orchestrator sits between the GUI side (the per-kind detector
cards) and cheshm's detector functions:

  - The controller registers one :class:`cheshm.gui.registry.Detector`
    per kind via :meth:`set_enabled_detectors`. ``None`` means the kind
    is not run (Off or Manual).
  - On image change, the caller invokes :meth:`clear_cache`.
  - :meth:`run_all` walks the enabled detectors in dependency order and
    runs each one. :meth:`run_one` re-runs a single kind reusing
    whichever upstream results are cached.

Upstream wiring is implicit, matching the contract every cheshm detector
already satisfies:

  - Glint detectors take ``pupil_center`` and ``pupil_radius`` as
    hidden keyword args. The orchestrator pops any user-supplied values
    and injects the cached pupil's centre + max-axis radius.
  - Limbus / eyelid detectors take the pupil centre (and optionally the
    pupil ellipse) as positional args after ``img``. The orchestrator
    passes them positionally regardless of the parameter name cheshm
    used in its own signature.

Two signals carry outcomes outward:

  - ``detector_ready(kind, result)`` after a successful call.
  - ``detector_failed(kind)`` when the call returned ``None`` or a
    required upstream result is missing.
"""

from collections.abc import Callable

import numpy as np
from cheshm.gui.registry import Detector
from PyQt5.QtCore import QObject, pyqtSignal

PostProcess = Callable[[dict], dict]

KINDS = ("pupil", "glint", "limbus", "eyelid")


class DetectorOrchestrator(QObject):
    """Dependency-aware runner + per-image result cache for cheshm detectors."""

    detector_ready = pyqtSignal(str, dict)
    detector_failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._enabled: dict[str, Detector | None] = dict.fromkeys(KINDS, None)
        self._results: dict[str, dict | None] = dict.fromkeys(KINDS, None)

    # ----- configuration -----

    def set_enabled_detectors(self, per_kind: dict[str, Detector | None]) -> None:
        """Replace the active detector set; wipe the cache for kinds that changed."""
        for kind in KINDS:
            new_det = per_kind.get(kind)
            if self._enabled[kind] is not new_det:
                self._enabled[kind] = new_det
                self._results[kind] = None

    def enabled_detector(self, kind: str) -> Detector | None:
        return self._enabled.get(kind)

    # ----- cache -----

    def cached_result(self, kind: str) -> dict | None:
        return self._results.get(kind)

    def set_cached_result(self, kind: str, result: dict | None) -> None:
        if kind not in self._results:
            raise ValueError(f"unknown kind {kind!r}")
        self._results[kind] = result

    def clear_cache(self) -> None:
        for kind in KINDS:
            self._results[kind] = None

    # ----- run paths -----

    def run_all(
        self,
        image: np.ndarray,
        params_by_kind: dict[str, dict],
        post_process_by_kind: dict[str, PostProcess] | None = None,
    ) -> None:
        """Run every enabled detector on ``image`` in dependency order.

        ``post_process_by_kind[kind]`` runs against ``kind``'s result
        before the cache write, used by callers that need to translate
        cropped results back into full-image coordinates.
        """
        self.clear_cache()
        post = post_process_by_kind or {}
        for kind in self._dependency_order():
            det = self._enabled[kind]
            if det is None:
                continue
            params = params_by_kind.get(kind) or self._default_params(det)
            self._run(kind, det, image, params, post_process=post.get(kind))

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
        det: Detector,
        image: np.ndarray,
        params: dict,
        post_process: PostProcess | None = None,
    ) -> None:
        kwargs = dict(params)
        wired_args: list = []
        if det.kind == "glint":
            self._inject_pupil_kwargs_for_glint(kwargs)
        elif det.kind in ("limbus", "eyelid"):
            wired_args = self._positional_pupil_for_limbus(det)
            if wired_args is None:
                self._results[kind] = None
                self.detector_failed.emit(kind)
                return
        try:
            result = det.function(image, *wired_args, **kwargs)
        except Exception:  # noqa: BLE001 - surface every detector failure to the status bar
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
        if not pupil:
            return
        ellipse = pupil.get("ellipse")
        if ellipse is None:
            return
        (_cx, _cy), (w, h), _angle = ellipse
        kwargs["pupil_center"] = pupil["center"]
        kwargs["pupil_radius"] = max(float(w), float(h)) / 2.0

    def _positional_pupil_for_limbus(self, det: Detector) -> list | None:
        """Return the positional args limbus/eyelid detectors expect after ``img``.

        Every limbus detector takes the pupil centre as its 2nd
        positional, and any detector with a 3rd positional gets the
        pupil ellipse there. The parameter names in the cheshm signature
        (``seed_center``, ``pupil_ellipse``, etc.) are irrelevant — we
        match by position against ``det.wired_inputs``.
        """
        pupil = self._results.get("pupil")
        if not pupil:
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

    @staticmethod
    def _default_params(det: Detector) -> dict:
        return {s.name: s.default for s in det.settings}

    def _dependency_order(self) -> list[str]:
        """Order enabled kinds so each runs after every kind it would consume."""
        return [k for k in KINDS if self._enabled.get(k) is not None]


__all__ = ["KINDS", "DetectorOrchestrator"]
