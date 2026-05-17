"""Per-image annotation persistence.

On-disk schema (binocular)::

    {
      "binocular_mode": true,
      "divider_x_norm": 0.5,            // optional; null => use project default
      "manual": {
        "left":  {pupil_points, limbus_points, eyelid_contour_points,
                  glint_points, pupil_ellipse, limbus_ellipse},
        "right": {...}
      },
      "detections": {
        "<plugin_name>": {"params": {...}, "result": {...}}
      }
    }

On-disk schema (monocular)::

    {
      "binocular_mode": false,
      "manual": {pupil_points, limbus_points, eyelid_contour_points,
                 glint_points, pupil_ellipse, limbus_ellipse},
      "detections": {
        "<plugin_name>": {"params": {...}, "result": {...}}
      }
    }

In monocular mode the ``manual`` block is **flat** — there is no
``left`` / ``right`` wrapper because the image carries a single eye
and labelling it left or right would be misleading. The image-viewer
in-memory store still keeps a `{"left": ..., "right": ...}` pair for
uniform internal handling; this module normalises both schemas to that
shape on load and back to flat on save.

The ``detections`` map holds one entry per detector plugin that has
been exercised on this image. Each entry's ``result`` is whatever the
plugin's ``serialize`` produced; ``params`` is the parameter values
used to run the detector. There is no schema constraint on the
``result`` shape — it is opaque to this module and only the plugin's
``deserialize`` knows how to interpret it.

This schema is the current source of truth; previous incompatible
shapes are not migrated.
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
    *,
    binocular_mode: bool,
    divider_x_norm: float | None = None,
    detections: dict | None = None,
) -> None:
    """Write the per-image annotation JSON.

    ``eye_data`` is the in-memory ``{"left": {...}, "right": {...}}``
    shape produced by the image viewer. In monocular mode only the
    ``left`` slot is meaningful and gets written as a flat ``manual``
    block; the ``right`` slot is dropped.

    ``divider_x_norm`` is the per-image divider override expressed as a
    fraction of image width in ``[0, 1]``. Pass ``None`` to let the
    image inherit the project default at load time. Only persisted
    when ``binocular_mode`` is True.
    """
    payload: dict = {"binocular_mode": bool(binocular_mode)}
    if binocular_mode:
        if divider_x_norm is not None:
            payload["divider_x_norm"] = float(divider_x_norm)
        payload["manual"] = {
            "left": _serialize_eye_block(eye_data["left"]),
            "right": _serialize_eye_block(eye_data["right"]),
        }
    else:
        payload["manual"] = _serialize_eye_block(eye_data["left"])
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


def _empty_payload(*, binocular_mode: bool = True) -> dict:
    """Return the standard 'no annotation file' return tuple payload."""
    return {
        "eye_data": {"left": _empty_eye_block(), "right": _empty_eye_block()},
        "detections": {},
        "binocular_mode": binocular_mode,
        "divider_x_norm": None,
    }


def load_annotations(annotation_path: str) -> dict:
    """Read a per-image annotation JSON and return a normalised payload.

    Returns a dict with keys ``eye_data``, ``detections``,
    ``binocular_mode`` and ``divider_x_norm``. ``eye_data`` is always
    shaped as ``{"left": ..., "right": ...}`` regardless of the on-disk
    schema — monocular files load their flat ``manual`` block into the
    ``left`` slot and an empty block into the ``right`` slot so the
    image viewer's internal store can stay uniform.

    ``divider_x_norm`` is ``None`` when the file carries no per-image
    override; callers fall back to the project-level default in that
    case.
    """
    if not Path(annotation_path).exists():
        return _empty_payload()
    try:
        with Path(annotation_path).open(encoding="utf-8") as f:
            ann = json.load(f)
    except json.JSONDecodeError as exc:
        # Corrupt or partially-written file (e.g. previous crash mid-save).
        # Don't crash the GUI; treat as no saved annotations.
        print(f"warning: skipping unreadable annotation file {annotation_path}: {exc}")
        return _empty_payload()

    binocular = bool(ann.get("binocular_mode", True))
    divider = ann.get("divider_x_norm")
    divider_x_norm = float(divider) if isinstance(divider, (int, float)) else None

    manual = ann.get("manual", {}) or {}
    if binocular:
        left_in = manual.get("left") or {}
        right_in = manual.get("right") or {}
        eye_data = {
            "left": _deserialize_eye_block(left_in) if left_in else _empty_eye_block(),
            "right": _deserialize_eye_block(right_in) if right_in else _empty_eye_block(),
        }
    else:
        eye_data = {
            "left": _deserialize_eye_block(manual) if manual else _empty_eye_block(),
            "right": _empty_eye_block(),
        }

    detections = ann.get("detections", {})
    if not isinstance(detections, dict):
        detections = {}
    return {
        "eye_data": eye_data,
        "detections": detections,
        "binocular_mode": binocular,
        "divider_x_norm": divider_x_norm,
    }


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
