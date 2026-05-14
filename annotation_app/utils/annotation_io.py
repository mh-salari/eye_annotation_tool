"""Functions for saving and loading annotation data."""

import json
from itertools import starmap
from pathlib import Path

from PyQt5.QtCore import QPointF, QSizeF


def _serialize_eye_block(block: dict) -> dict:
    """Convert one in-memory eye block to its JSON-serialisable form."""
    return {
        "pupil_points": [(p.x(), p.y()) for p in block["pupil_points"]],
        "iris_points": [(p.x(), p.y()) for p in block["iris_points"]],
        "eyelid_contour_points": [(p.x(), p.y()) for p in block["eyelid_contour_points"]],
        "glint_points": [(p.x(), p.y()) for p in block["glint_points"]],
        "pupil_ellipse": ellipse_to_dict(block["pupil_ellipse"]),
        "iris_ellipse": ellipse_to_dict(block["iris_ellipse"]),
        "roi": block.get("roi"),
    }


def save_annotations(
    annotation_path: str,
    eye_data: dict,
    single_eye_mode: bool = False,
) -> None:
    """Save annotation data to a JSON file.

    Args:
        annotation_path: Path where the annotation file will be saved.
        eye_data: Dictionary containing annotation data for both left and right eyes.
        single_eye_mode: When True, write a flat schema (no ``left`` / ``right``
            keys) using the ``left`` block as the canonical single-eye source.

    """
    if single_eye_mode:
        serializable_data = _serialize_eye_block(eye_data["left"])
    else:
        serializable_data = {eye: _serialize_eye_block(eye_data[eye]) for eye in ("left", "right")}

    with Path(annotation_path).open("w", encoding="utf-8") as f:
        json.dump(serializable_data, f, indent=2)


def _empty_eye_block() -> dict:
    return {
        "pupil_points": [],
        "iris_points": [],
        "eyelid_contour_points": [],
        "glint_points": [],
        "pupil_ellipse": None,
        "iris_ellipse": None,
        "roi": None,
    }


def _deserialize_eye_block(block: dict) -> dict:
    """Convert one on-disk eye block (or a flat schema dict) back to in-memory form."""
    roi_data = block.get("roi")
    roi = tuple(roi_data) if roi_data and isinstance(roi_data, (list, tuple)) and len(roi_data) == 4 else None
    return {
        "pupil_points": list(starmap(QPointF, block.get("pupil_points", []))),
        "iris_points": list(starmap(QPointF, block.get("iris_points", []))),
        "eyelid_contour_points": list(starmap(QPointF, block.get("eyelid_contour_points", []))),
        "glint_points": list(starmap(QPointF, block.get("glint_points", []))),
        "pupil_ellipse": dict_to_ellipse(block.get("pupil_ellipse")),
        "iris_ellipse": dict_to_ellipse(block.get("iris_ellipse")),
        "roi": roi,
    }


def load_annotations(annotation_path: str) -> dict:
    """Load annotation data from a JSON file.

    Handles three on-disk schemas transparently:

    - **Multi-eye** (current default): top-level keys ``"left"`` and/or
      ``"right"`` map to per-eye blocks.
    - **Flat single-eye**: top-level keys are the annotation fields
      (``"pupil_points"``, ...). Loaded into the ``"left"`` block; the
      ``"right"`` block is left empty.
    - **Legacy single-eye** (pre-multi-eye refactor): same shape as the flat
      single-eye format. Treated identically.

    Returns:
        ``{"left": {...}, "right": {...}}`` regardless of on-disk schema, so
        the rest of the app sees a uniform structure.

    """
    if not Path(annotation_path).exists():
        return {"left": _empty_eye_block(), "right": _empty_eye_block()}

    with Path(annotation_path).open(encoding="utf-8") as f:
        ann = json.load(f)

    if "left" in ann or "right" in ann:
        return {
            "left": _deserialize_eye_block(ann["left"]) if "left" in ann else _empty_eye_block(),
            "right": _deserialize_eye_block(ann["right"]) if "right" in ann else _empty_eye_block(),
        }
    # Flat single-eye schema (or legacy pre-multi-eye file). Load into left.
    return {"left": _deserialize_eye_block(ann), "right": _empty_eye_block()}


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
