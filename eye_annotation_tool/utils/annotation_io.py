"""Per-image annotation persistence.

On-disk schema::

    {
      "binocular_mode": true,
      "divider_x_norm": 0.5,            // optional; null => use project default
      "detections": {
        "pupil": {
          "left":  {"id": "Simple" | "manual" | ...,
                     "params": {...},
                     "result": {...}},
          "right": {...}
        },
        "glint": {...}, "limbus": {...}, "eyelid": {...}
      }
    }

Every annotation — detector and manual alike — lives in ``detections``
as one per-eye ``{id, params, result}`` block per kind (monocular uses a
single ``"single"`` slot). A manual block carries the clicked ``points``
(and fit settings) in ``params`` and the fitted ellipse / smooth curve in
``result``; a detector block carries its settings and serialized result.
The detection controller owns translating these blocks to and from the
image viewer's in-memory state. This module keeps the I/O opaque to the
block contents.

This schema is the current source of truth; previous shapes are migrated
out-of-band, not read here.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def save_annotations(
    annotation_path: str,
    *,
    binocular_mode: bool,
    divider_x_norm: float | None = None,
    detections: dict | None = None,
    enhancement: dict | None = None,
) -> None:
    """Write the per-image annotation JSON.

    All annotation data — manual and detector alike — lives in
    ``detections`` (one per-eye ``{id, params, result}`` block per kind);
    there is no separate ``manual`` block.

    ``divider_x_norm`` is the per-image divider override expressed as a
    fraction of image width in ``[0, 1]``. Pass ``None`` to let the
    image inherit the project default at load time. Only persisted
    when ``binocular_mode`` is True.

    ``enhancement`` records the image preprocessing fed to the detector for
    this image (provenance: what produced these results). Pass ``None`` when
    the enhancement was display-only and did not touch detection.
    """
    payload: dict = {"binocular_mode": bool(binocular_mode)}
    if binocular_mode and divider_x_norm is not None:
        payload["divider_x_norm"] = float(divider_x_norm)
    payload["detections"] = dict(detections) if detections else {}
    if enhancement is not None:
        payload["enhancement"] = dict(enhancement)
    with Path(annotation_path).open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _empty_payload(*, binocular_mode: bool = True) -> dict:
    """Return the standard 'no annotation file' return payload."""
    return {
        "detections": {},
        "binocular_mode": binocular_mode,
        "divider_x_norm": None,
    }


def load_annotations(annotation_path: str) -> dict:
    """Read a per-image annotation JSON and return a normalised payload.

    Returns a dict with keys ``detections``, ``binocular_mode`` and
    ``divider_x_norm``. Manual annotations live inside ``detections`` (one
    per-eye ``{id, params, result}`` block per kind), so there is no separate
    ``eye_data``; the detection controller reconstructs the viewer state from
    the detections.

    ``divider_x_norm`` is ``None`` when the file carries no per-image
    override; callers fall back to the project-level default in that case.
    """
    if not Path(annotation_path).exists():
        return _empty_payload()
    try:
        with Path(annotation_path).open(encoding="utf-8") as f:
            ann = json.load(f)
    except json.JSONDecodeError:
        # Corrupt/partially-written file (e.g. crash mid-save): log and treat as
        # no saved annotations rather than crash the GUI on an external file.
        logger.warning("skipping unreadable annotation file %s", annotation_path, exc_info=True)
        return _empty_payload()

    binocular = bool(ann.get("binocular_mode", True))
    divider = ann.get("divider_x_norm")
    divider_x_norm = float(divider) if isinstance(divider, (int, float)) else None
    detections = ann.get("detections", {})
    if not isinstance(detections, dict):
        detections = {}
    return {
        "detections": detections,
        "binocular_mode": binocular,
        "divider_x_norm": divider_x_norm,
    }


def get_annotation_path(image_path: str) -> str:
    """Return the annotation file path for ``image_path``."""
    path = Path(image_path)
    return str(path.parent / f"{path.stem}_annotation.json")
