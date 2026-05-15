"""Functions for saving and loading annotation data."""

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
        "roi": block.get("roi"),
    }


def _serialize_tuning(tuning: dict | None) -> dict | None:
    """Convert in-memory Manual-Threshold tuning state to JSON form."""
    if tuning is None:
        return None
    return {
        "thresholds": tuning.get("thresholds"),
        "pupil_roi": list(tuning["pupil_roi"]) if tuning.get("pupil_roi") else None,
        "glint_roi": list(tuning["glint_roi"]) if tuning.get("glint_roi") else None,
        "detection": tuning.get("detection"),
    }


def _deserialize_tuning(raw: dict | None) -> dict | None:
    """Restore Manual-Threshold tuning state from JSON form."""
    if raw is None:
        return None
    return {
        "thresholds": raw.get("thresholds"),
        "pupil_roi": tuple(raw["pupil_roi"]) if raw.get("pupil_roi") else None,
        "glint_roi": tuple(raw["glint_roi"]) if raw.get("glint_roi") else None,
        "detection": raw.get("detection"),
    }


def save_annotations(
    annotation_path: str,
    eye_data: dict,
    single_eye_mode: bool = False,
    tuning: dict | None = None,
) -> None:
    """Save annotation data to a JSON file.

    Args:
        annotation_path: Path where the annotation file will be saved.
        eye_data: Dictionary containing annotation data for both left and right eyes.
        single_eye_mode: When True, write a flat schema (no ``left`` / ``right``
            keys) using the ``left`` block as the canonical single-eye source.
        tuning: Optional Manual-Threshold tuning state (thresholds + ROIs +
            cached detection). Written as a top-level ``tuning`` key alongside
            the eye blocks.

    """
    if single_eye_mode:
        serializable_data = _serialize_eye_block(eye_data["left"])
    else:
        serializable_data = {eye: _serialize_eye_block(eye_data[eye]) for eye in ("left", "right")}

    if tuning is not None:
        serializable_data["tuning"] = _serialize_tuning(tuning)

    with Path(annotation_path).open("w", encoding="utf-8") as f:
        json.dump(serializable_data, f, indent=2)


def _empty_eye_block() -> dict:
    return {
        "pupil_points": [],
        "limbus_points": [],
        "eyelid_contour_points": [],
        "glint_points": [],
        "pupil_ellipse": None,
        "limbus_ellipse": None,
        "roi": None,
    }


def _deserialize_eye_block(block: dict) -> dict:
    """Convert one on-disk eye block (or a flat schema dict) back to in-memory form."""
    roi_data = block.get("roi")
    roi = tuple(roi_data) if roi_data and isinstance(roi_data, (list, tuple)) and len(roi_data) == 4 else None
    return {
        "pupil_points": list(starmap(QPointF, block.get("pupil_points", []))),
        "limbus_points": list(starmap(QPointF, block.get("limbus_points", []))),
        "eyelid_contour_points": list(starmap(QPointF, block.get("eyelid_contour_points", []))),
        "glint_points": list(starmap(QPointF, block.get("glint_points", []))),
        "pupil_ellipse": dict_to_ellipse(block.get("pupil_ellipse")),
        "limbus_ellipse": dict_to_ellipse(block.get("limbus_ellipse")),
        "roi": roi,
    }


def load_annotations(annotation_path: str) -> tuple[dict, dict | None]:
    """Load annotation data from a JSON file.

    Accepts two on-disk shapes:

    - **Multi-eye**: top-level keys ``"left"`` and/or ``"right"`` mapping to
      per-eye blocks.
    - **Flat single-eye**: top-level keys are the annotation fields
      themselves (``"pupil_points"``, ...). Loaded into the ``"left"`` block;
      ``"right"`` stays empty.

    Returns ``(eye_data, tuning)``. ``eye_data`` is always ``{"left": ...,
    "right": ...}``; ``tuning`` is the deserialised Manual-Threshold state
    or ``None`` when the file has no ``"tuning"`` key.
    """
    if not Path(annotation_path).exists():
        return {"left": _empty_eye_block(), "right": _empty_eye_block()}, None

    try:
        with Path(annotation_path).open(encoding="utf-8") as f:
            ann = json.load(f)
    except json.JSONDecodeError as exc:
        # Corrupt or partially-written file (e.g. previous crash mid-save).
        # Don't crash the GUI; treat as no saved annotations.
        print(f"warning: skipping unreadable annotation file {annotation_path}: {exc}")
        return {"left": _empty_eye_block(), "right": _empty_eye_block()}, None

    tuning = _deserialize_tuning(ann.get("tuning"))
    if "left" in ann or "right" in ann:
        eye_data = {
            "left": _deserialize_eye_block(ann["left"]) if "left" in ann else _empty_eye_block(),
            "right": _deserialize_eye_block(ann["right"]) if "right" in ann else _empty_eye_block(),
        }
    else:
        # Flat single-eye schema: top-level keys ARE the annotation fields.
        # Load the whole document into the left block; right stays empty.
        eye_data = {"left": _deserialize_eye_block(ann), "right": _empty_eye_block()}
    return eye_data, tuning


def get_annotation_path(image_path: str) -> str:
    """Get the annotation file path for a given image.

    Args:
        image_path: Path to the image file.

    Returns:
        Path to the corresponding annotation file.

    """
    path = Path(image_path)
    return str(path.parent / f"{path.stem}_annotation.json")


def ellipse_to_dict(ellipse: tuple | None) -> dict | None:
    """Convert ellipse tuple to dictionary format.

    Args:
        ellipse: Ellipse parameters as tuple or None.

    Returns:
        Dictionary with ellipse parameters or None.

    """
    if ellipse is None:
        return None
    center, size, angle = ellipse
    return {
        "center": (center.x(), center.y()),
        "size": (size.width(), size.height()),
        "angle": angle,
    }


def dict_to_ellipse(ellipse_dict: dict | None) -> tuple | None:
    """Convert ellipse dictionary to tuple format.

    Args:
        ellipse_dict: Dictionary with ellipse parameters or None.

    Returns:
        Ellipse parameters as tuple or None.

    """
    if ellipse_dict is None:
        return None
    center = QPointF(*ellipse_dict["center"])
    size = QSizeF(*ellipse_dict["size"])
    angle = ellipse_dict["angle"]
    return (center, size, angle)
