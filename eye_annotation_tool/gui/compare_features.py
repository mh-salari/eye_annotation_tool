"""Read an image's saved glints and detection results for Compare mode.

Compare overlays the two images' *saved* detections; these helpers load them
straight from each image's ``<stem>_annotation.json`` so Compare never re-runs a
detector. Glints drive the matched-glint alignment; the per-kind results drive
the overlay drawing.
"""

import numpy as np

from ..utils.annotation_io import get_annotation_path, load_annotations

# Detection slots in priority order: monocular images use "single", binocular
# images split into "left" / "right". Compare reads whichever are populated.
_SLOTS = ("single", "left", "right")


def load_glints(image_path: str) -> np.ndarray:
    """Every saved glint centre across all slots, as an ``(N, 2)`` array."""
    det = load_annotations(get_annotation_path(image_path)).get("detections", {})
    centres: list[list[float]] = []
    for slot in _SLOTS:
        result = (det.get("glint", {}).get(slot) or {}).get("result") or {}
        centres.extend(g["center"] for g in result.get("glints", []) if g.get("center") is not None)
    return np.array(centres, float) if centres else np.empty((0, 2))


def load_results(image_path: str) -> list[tuple[str, str, dict]]:
    """Saved detections as ``(kind, detector_id, result)`` for every populated slot."""
    det = load_annotations(get_annotation_path(image_path)).get("detections", {})
    out: list[tuple[str, str, dict]] = []
    for kind, block in det.items():
        if not isinstance(block, dict):
            continue
        for slot in _SLOTS:
            entry = block.get(slot) or {}
            result = entry.get("result")
            if result:
                out.append((kind, entry.get("id", "manual"), result))
    return out
