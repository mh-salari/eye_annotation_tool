import os
import json
from PyQt5.QtCore import QPointF, QSizeF


def save_annotations(
    annotation_path,
    pupil_points,
    iris_points,
    eyelid_contour_points,
    glint_points,
    pupil_ellipse,
    iris_ellipse,
):
    current_annotation = {
        "pupil_points": [(p.x(), p.y()) for p in pupil_points],
        "iris_points": [(p.x(), p.y()) for p in iris_points],
        "eyelid_contour_points": [(p.x(), p.y()) for p in eyelid_contour_points],
        "glint_points": [(p.x(), p.y()) for p in glint_points],
        "pupil_ellipse": ellipse_to_dict(pupil_ellipse),
        "iris_ellipse": ellipse_to_dict(iris_ellipse),
    }
    with open(annotation_path, "w") as f:
        json.dump(current_annotation, f, indent=2)


def load_annotations(annotation_path):
    if os.path.exists(annotation_path):
        with open(annotation_path, "r") as f:
            ann = json.load(f)
        return {
            "pupil_points": [QPointF(x, y) for x, y in ann.get("pupil_points", [])],
            "iris_points": [QPointF(x, y) for x, y in ann.get("iris_points", [])],
            "eyelid_contour_points": [
                QPointF(x, y) for x, y in ann.get("eyelid_contour_points", [])
            ],
            "glint_points": [QPointF(x, y) for x, y in ann.get("glint_points", [])],
            "pupil_ellipse": dict_to_ellipse(ann.get("pupil_ellipse")),
            "iris_ellipse": dict_to_ellipse(ann.get("iris_ellipse")),
        }
    else:
        return {
            "pupil_points": [],
            "iris_points": [],
            "eyelid_contour_points": [],
            "glint_points": [],
            "pupil_ellipse": None,
            "iris_ellipse": None,
        }


def get_annotation_path(image_path):
    directory = os.path.dirname(image_path)
    filename = os.path.splitext(os.path.basename(image_path))[0]
    return os.path.join(directory, f"{filename}_annotation.json")


def ellipse_to_dict(ellipse):
    if ellipse is None:
        return None
    center, size, angle = ellipse
    return {
        "center": (center.x(), center.y()),
        "size": (size.width(), size.height()),
        "angle": angle,
    }


def dict_to_ellipse(ellipse_dict):
    if ellipse_dict is None:
        return None
    center = QPointF(*ellipse_dict["center"])
    size = QSizeF(*ellipse_dict["size"])
    angle = ellipse_dict["angle"]
    return (center, size, angle)
