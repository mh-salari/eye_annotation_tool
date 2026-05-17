"""Per-image annotation persistence.

On-disk schema::

    {
      "single_eye_mode": false,
      "manual": {
        "left":  {pupil_points, limbus_points, eyelid_contour_points,
                  glint_points, pupil_ellipse, limbus_ellipse, roi},
        "right": {...}
      },
      "detections": {
        "<plugin_name>": {"params": {...}, "result": {...}}
      }
    }

When ``single_eye_mode`` is true, the ``manual`` block contains only the
``left`` key — single-eye annotations are written into the left block.

The ``detections`` map holds one entry per detector plugin that has been
exercised on this image. Each entry's ``result`` is whatever the plugin's
``serialize`` produced; ``params`` is the parameter values used to run
the detector. There is no schema constraint on the ``result`` shape — it
is opaque to this module and only the plugin's ``deserialize`` knows how
to interpret it.

This is a breaking schema. Older annotation files (with top-level
``left`` / ``right`` keys and a ``tuning`` blob) are not migrated and
will not load.
"""

import json
from itertools import starmap
from pathlib import Path

from PyQt5.QtCore import QPointF, QSizeF


def _serialize_eye_block(block: dict) -> dict:
    """Convert one in-memory eye block to its JSON-serialisable form."""
    return {
        "pupil_points": [(p.x(), p.y()) for p in block["pupil_points"]],
        "limbus_points": [(p.x(), p.y()) for p in block["limbus_points"]],
        "eyelid_contour_points": [(p.x(), p.y()) for p in block["eyelid_contour_points"]],
        "glint_points": [(p.x(), p.y()) for p in block["glint_points"]],
        "pupil_ellipse": ellipse_to_dict(block["pupil_ellipse"]),
        "limbus_ellipse": ellipse_to_dict(block["limbus_ellipse"]),
    }


def save_annotations(
    annotation_path: str,
    eye_data: dict,
    single_eye_mode: bool,
    detections: dict | None = None,
) -> None:
    """Write the per-image annotation JSON.

    ``eye_data`` is the in-memory ``{"left": {...}, "right": {...}}`` shape
    produced by the image viewer. ``detections`` is the dict the
    AnnotationController assembles from each enabled plugin's serialised
    result; pass ``None`` (or ``{}``) when no detection results need to
    be persisted.
    """
    payload: dict = {
        "single_eye_mode": bool(single_eye_mode),
        "manual": {"left": _serialize_eye_block(eye_data["left"])},
    }
    if not single_eye_mode:
        payload["manual"]["right"] = _serialize_eye_block(eye_data["right"])
    payload["detections"] = dict(detections) if detections else {}
    with Path(annotation_path).open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _empty_eye_block() -> dict:
    return {
        "pupil_points": [],
        "limbus_points": [],
        "eyelid_contour_points": [],
        "glint_points": [],
        "pupil_ellipse": None,
        "limbus_ellipse": None,
    }


def _deserialize_eye_block(block: dict) -> dict:
    """Convert one on-disk eye block back to in-memory form."""
    return {
        "pupil_points": list(starmap(QPointF, block.get("pupil_points", []))),
        "limbus_points": list(starmap(QPointF, block.get("limbus_points", []))),
        "eyelid_contour_points": list(starmap(QPointF, block.get("eyelid_contour_points", []))),
        "glint_points": list(starmap(QPointF, block.get("glint_points", []))),
        "pupil_ellipse": dict_to_ellipse(block.get("pupil_ellipse")),
        "limbus_ellipse": dict_to_ellipse(block.get("limbus_ellipse")),
    }


def load_annotations(annotation_path: str) -> tuple[dict, dict]:
    """Read a per-image annotation JSON.

    Returns ``(eye_data, detections)`` where ``eye_data`` is always
    ``{"left": ..., "right": ...}`` (the right block is empty in
    single-eye files) and ``detections`` is the raw on-disk
    plugin-name → ``{"params": ..., "result": ...}`` map (each plugin
    deserialises its own block — this module does not interpret them).
    """
    if not Path(annotation_path).exists():
        return {"left": _empty_eye_block(), "right": _empty_eye_block()}, {}
    try:
        with Path(annotation_path).open(encoding="utf-8") as f:
            ann = json.load(f)
    except json.JSONDecodeError as exc:
        # Corrupt or partially-written file (e.g. previous crash mid-save).
        # Don't crash the GUI; treat as no saved annotations.
        print(f"warning: skipping unreadable annotation file {annotation_path}: {exc}")
        return {"left": _empty_eye_block(), "right": _empty_eye_block()}, {}
    manual = ann.get("manual", {})
    left_in = manual.get("left", {})
    right_in = manual.get("right", {})
    eye_data = {
        "left": _deserialize_eye_block(left_in) if left_in else _empty_eye_block(),
        "right": _deserialize_eye_block(right_in) if right_in else _empty_eye_block(),
    }
    detections = ann.get("detections", {})
    if not isinstance(detections, dict):
        detections = {}
    return eye_data, detections


def get_annotation_path(image_path: str) -> str:
    """Return the annotation file path for ``image_path``."""
    path = Path(image_path)
    return str(path.parent / f"{path.stem}_annotation.json")


def ellipse_to_dict(ellipse: tuple | None) -> dict | None:
    """Convert a ``(QPointF, QSizeF, angle)`` ellipse tuple to a JSON-friendly dict."""
    if ellipse is None:
        return None
    center, size, angle = ellipse
    return {
        "center": (center.x(), center.y()),
        "size": (size.width(), size.height()),
        "angle": angle,
    }


def dict_to_ellipse(ellipse_dict: dict | None) -> tuple | None:
    """Convert a serialised ellipse dict back to ``(QPointF, QSizeF, angle)``."""
    if ellipse_dict is None:
        return None
    center = QPointF(*ellipse_dict["center"])
    size = QSizeF(*ellipse_dict["size"])
    angle = ellipse_dict["angle"]
    return (center, size, angle)
