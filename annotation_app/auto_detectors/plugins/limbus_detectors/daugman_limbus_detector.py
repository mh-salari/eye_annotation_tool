"""Daugman integro-differential limbus detector."""

import cv2
import numpy as np

from annotation_app.auto_detectors.algorithms.limbus import detect_limbus
from annotation_app.auto_detectors.plugin_interface import DetectorPlugin

# Points sampled around the limbus circle so the GUI's ellipse-fit / draw
# paths get a usable polyline.
LIMBUS_POINT_COUNT = 32


class DaugmanLimbusDetector(DetectorPlugin):
    """Locate the iris-sclera boundary via the Daugman integro-differential operator.

    The IDO needs a pupil centre + radius to seed its annular search. The
    caller must call :meth:`set_pupil_seed` before :meth:`detect`; otherwise
    detect raises ``ValueError`` so the GUI can prompt the user to annotate
    or auto-detect the pupil first.
    """

    def __init__(self) -> None:
        """Initialise the detector."""
        self._pupil_seed: tuple[tuple[float, float], float] | None = None

    def set_pupil_seed(self, center: tuple[float, float], radius: float) -> None:
        """Provide the pupil centre + radius the IDO uses to seed its search."""
        self._pupil_seed = ((float(center[0]), float(center[1])), float(radius))

    def detect(self, image_path: str) -> tuple[dict, list]:
        """Return ``(ellipse_dict, points_on_circle)`` for the detected limbus."""
        if self._pupil_seed is None:
            raise ValueError("Daugman limbus detector requires a pupil seed; annotate the pupil first.")
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError(f"failed to read image: {image_path}")
        (pcx, pcy), pupil_radius = self._pupil_seed

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
