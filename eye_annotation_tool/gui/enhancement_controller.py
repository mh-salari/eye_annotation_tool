"""Image-enhancement pipeline for the viewer, backed by ``cheshm.enhance``.

A pipeline of one or more enhancement stages (CLAHE / percentile-stretch /
gamma / bilateral / unsharp), each with its own params, applied in order to the
source grayscale. Two consumers:

- the display always shows the enhanced image (so boundaries are easier to see),
- the detector receives the enhanced image only when ``apply_to_detection`` is set
  (otherwise it keeps seeing the raw source-of-truth grayscale).

The enhanced array is cached and only recomputed when the source array or the
pipeline changes.
"""

import numpy as np
from cheshm import enhance

METHODS = ("clahe", "percentile_stretch", "gamma", "bilateral", "unsharp")

Stage = tuple[str, dict[str, float]]


class EnhancementController:
    """Holds an ordered enhancement pipeline + an apply-to-detection flag; caches the result."""

    def __init__(self) -> None:
        """Start with an empty pipeline: ``apply`` returns the input unchanged."""
        self.stages: list[Stage] = []
        self.apply_to_detection: bool = False
        self._cache_key: tuple | None = None
        self._cache: np.ndarray | None = None

    def configure(self, stages: list[Stage], apply_to_detection: bool) -> None:
        """Set the ordered pipeline and the detection flag; invalidate the cache."""
        for method, _ in stages:
            if method not in METHODS:
                raise ValueError(f"unknown enhancement {method!r}; valid: {METHODS}")
        self.stages = [(m, dict(p)) for m, p in stages]
        self.apply_to_detection = bool(apply_to_detection)
        self._cache_key = None
        self._cache = None

    def signature(self) -> tuple:
        """Hashable summary of the pipeline (order + params), for change detection."""
        return tuple((m, tuple(sorted(p.items()))) for m, p in self.stages)

    def is_active(self) -> bool:
        """True when the pipeline has at least one stage."""
        return bool(self.stages)

    def apply(self, gray: np.ndarray | None) -> np.ndarray | None:
        """Return the enhanced grayscale (cached); the input unchanged when empty."""
        if gray is None or not self.stages:
            return gray
        key = (id(gray), self.signature())
        if key == self._cache_key and self._cache is not None:
            return self._cache
        out = gray
        for method, params in self.stages:
            out = enhance.apply(out, method, **params)
        self._cache_key, self._cache = key, out
        return out

    def to_dict(self) -> dict:
        """Serialise for the project file."""
        return {
            "stages": [{"method": m, "params": dict(p)} for m, p in self.stages],
            "apply_to_detection": self.apply_to_detection,
        }

    def from_dict(self, data: dict | None) -> None:
        """Restore from a project file block (tolerant of missing/None)."""
        data = data or {}
        stages = [
            (s["method"], s.get("params") or {})
            for s in data.get("stages", [])
            if isinstance(s, dict) and s.get("method") in METHODS
        ]
        self.configure(stages, bool(data.get("apply_to_detection", False)))
