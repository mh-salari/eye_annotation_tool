"""Daugman integro-differential limbus detector."""

import cv2
import numpy as np

from annotation_app.auto_detectors.algorithms.limbus import detect_limbus
from annotation_app.auto_detectors.algorithms.pupil import detect_pupil_and_glints
from annotation_app.auto_detectors.plugin_interface import DetectorPlugin

# Points sampled around the limbus circle so the GUI's ellipse-fit / draw
# paths get a usable polyline.
LIMBUS_POINT_COUNT = 32


class DaugmanLimbusDetector(DetectorPlugin):
    """Locate the iris-sclera boundary via the Daugman integro-differential operator.

    Pupil is found first with the threshold-based detector so the IDO has a
    centre + radius to seed its annular search.
    """

    def __init__(self) -> None:
        """Initialise the detector."""

    def detect(self, image_path: str) -> tuple[dict, list]:  # noqa: PLR6301
        """Return ``(ellipse_dict, points_on_circle)`` for the detected limbus."""
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError(f"failed to read image: {image_path}")

        # Use Otsu to seed the pupil — same default the other auto-detector
        # plugins fall back to when no explicit threshold is set.
        blurred = cv2.GaussianBlur(img, (5, 5), 0)
        pupil_threshold, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        result = detect_pupil_and_glints(img, pupil_threshold=int(pupil_threshold))
        (pcx, pcy), (pw, ph), _ = result["pupil_ellipse"]
        pupil_radius = max(pw, ph) / 2

        (lcx, lcy), lr = detect_limbus(img, (pcx, pcy), pupil_radius)

        t = np.linspace(0, 2 * np.pi, LIMBUS_POINT_COUNT, endpoint=False)
        points = np.column_stack([lcx + lr * np.cos(t), lcy + lr * np.sin(t)])

        ellipse = {
            "center": (float(lcx), float(lcy)),
            "axes": (float(2 * lr), float(2 * lr)),
            "angle": 0.0,
        }
        return ellipse, points.tolist()

    @property
    def name(self) -> str:
        """Get the name of the detector plugin."""
        return "Daugman IDO"
