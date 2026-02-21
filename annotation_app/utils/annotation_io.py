"""Functions for saving and loading annotation data."""

import json
from itertools import starmap
from pathlib import Path

from PyQt5.QtCore import QPointF, QSizeF


def save_annotations(
    annotation_path: str,
    eye_data: dict,
) -> None:
    """Save annotation data for both eyes to a JSON file.

    Args:
        annotation_path: Path where the annotation file will be saved.
        eye_data: Dictionary containing annotation data for both left and right eyes.

    """
    # Convert eye_data to serializable format
    serializable_data = {}
    for eye in ["left", "right"]:
        serializable_data[eye] = {
            "pupil_points": [(p.x(), p.y()) for p in eye_data[eye]["pupil_points"]],
            "iris_points": [(p.x(), p.y()) for p in eye_data[eye]["iris_points"]],
            "eyelid_contour_points": [(p.x(), p.y()) for p in eye_data[eye]["eyelid_contour_points"]],
            "glint_points": [(p.x(), p.y()) for p in eye_data[eye]["glint_points"]],
            "pupil_ellipse": ellipse_to_dict(eye_data[eye]["pupil_ellipse"]),
            "iris_ellipse": ellipse_to_dict(eye_data[eye]["iris_ellipse"]),
            "roi": eye_data[eye].get("roi"),
        }

    with Path(annotation_path).open("w", encoding="utf-8") as f:
        json.dump(serializable_data, f, indent=2)


def load_annotations(annotation_path: str) -> dict:
    """Load annotation data for both eyes from a JSON file.

    Args:
        annotation_path: Path to the annotation file.

    Returns:
        Dictionary containing annotation data for both eyes.

    """
    empty_eye_data = {
        "pupil_points": [],
        "iris_points": [],
        "eyelid_contour_points": [],
        "glint_points": [],
        "pupil_ellipse": None,
        "iris_ellipse": None,
        "roi": None,
    }

    if Path(annotation_path).exists():
        with Path(annotation_path).open(encoding="utf-8") as f:
            ann = json.load(f)

        # Check if this is new format (with left/right) or old format (single eye)
        if "left" in ann or "right" in ann:
            # New format with both eyes
            eye_data = {}
            for eye in ["left", "right"]:
                if eye in ann:
                    roi_data = ann[eye].get("roi")
                    if roi_data and isinstance(roi_data, (list, tuple)) and len(roi_data) == 4:
                        roi = tuple(roi_data)
                    else:
                        roi = None

                    eye_data[eye] = {
                        "pupil_points": list(starmap(QPointF, ann[eye].get("pupil_points", []))),
                        "iris_points": list(starmap(QPointF, ann[eye].get("iris_points", []))),
                        "eyelid_contour_points": list(
                            starmap(QPointF, ann[eye].get("eyelid_contour_points", []))
                        ),
                        "glint_points": list(starmap(QPointF, ann[eye].get("glint_points", []))),
                        "pupil_ellipse": dict_to_ellipse(ann[eye].get("pupil_ellipse")),
                        "iris_ellipse": dict_to_ellipse(ann[eye].get("iris_ellipse")),
                        "roi": roi,
                    }
                else:
                    eye_data[eye] = empty_eye_data.copy()
            return eye_data
        # Old format (single eye) - migrate to left eye
        return {
            "left": {
                "pupil_points": list(starmap(QPointF, ann.get("pupil_points", []))),
                "iris_points": list(starmap(QPointF, ann.get("iris_points", []))),
                "eyelid_contour_points": list(starmap(QPointF, ann.get("eyelid_contour_points", []))),
                "glint_points": list(starmap(QPointF, ann.get("glint_points", []))),
                "pupil_ellipse": dict_to_ellipse(ann.get("pupil_ellipse")),
                "iris_ellipse": dict_to_ellipse(ann.get("iris_ellipse")),
                "roi": None,  # Old format doesn't have ROI
            },
            "right": empty_eye_data.copy(),
        }
    return {"left": empty_eye_data.copy(), "right": empty_eye_data.copy()}


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
