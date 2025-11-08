"""Functions for saving and loading annotation data."""

import json
from itertools import starmap
from pathlib import Path

from PyQt5.QtCore import QPointF, QSizeF


def save_annotations(
    annotation_path: str,
    pupil_points: list[QPointF],
    iris_points: list[QPointF],
    eyelid_contour_points: list[QPointF],
    glint_points: list[QPointF],
    pupil_ellipse: tuple | None,
    iris_ellipse: tuple | None,
) -> None:
    """Save annotation data to a JSON file.

    Args:
        annotation_path: Path where the annotation file will be saved.
        pupil_points: List of pupil annotation points.
        iris_points: List of iris annotation points.
        eyelid_contour_points: List of eyelid contour points.
        glint_points: List of glint points.
        pupil_ellipse: Pupil ellipse parameters or None.
        iris_ellipse: Iris ellipse parameters or None.

    """
    current_annotation = {
        "pupil_points": [(p.x(), p.y()) for p in pupil_points],
        "iris_points": [(p.x(), p.y()) for p in iris_points],
        "eyelid_contour_points": [(p.x(), p.y()) for p in eyelid_contour_points],
        "glint_points": [(p.x(), p.y()) for p in glint_points],
        "pupil_ellipse": ellipse_to_dict(pupil_ellipse),
        "iris_ellipse": ellipse_to_dict(iris_ellipse),
    }
    with Path(annotation_path).open("w", encoding="utf-8") as f:
        json.dump(current_annotation, f, indent=2)


def load_annotations(annotation_path: str) -> dict:
    """Load annotation data from a JSON file.

    Args:
        annotation_path: Path to the annotation file.

    Returns:
        Dictionary containing annotation data.

    """
    if Path(annotation_path).exists():
        with Path(annotation_path).open(encoding="utf-8") as f:
            ann = json.load(f)
        return {
            "pupil_points": list(starmap(QPointF, ann.get("pupil_points", []))),
            "iris_points": list(starmap(QPointF, ann.get("iris_points", []))),
            "eyelid_contour_points": list(starmap(QPointF, ann.get("eyelid_contour_points", []))),
            "glint_points": list(starmap(QPointF, ann.get("glint_points", []))),
            "pupil_ellipse": dict_to_ellipse(ann.get("pupil_ellipse")),
            "iris_ellipse": dict_to_ellipse(ann.get("iris_ellipse")),
        }
    return {
        "pupil_points": [],
        "iris_points": [],
        "eyelid_contour_points": [],
        "glint_points": [],
        "pupil_ellipse": None,
        "iris_ellipse": None,
    }


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
