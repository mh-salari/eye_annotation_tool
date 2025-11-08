"""Pupil detector using the Pupil Core library."""

import cv2
import numpy as np
from pupil_detectors import Detector2D

from ai.plugin_interface import DetectorPlugin


class PupilCoreDetector(DetectorPlugin):
    """Pupil detector implementation using Pupil Core's 2D detector."""

    def __init__(self) -> None:
        """Initialize the PupilCoreDetector."""
        self.detector = Detector2D()

    def detect(self, image_path: str) -> tuple[dict, list]:
        """Detect pupil in the given image using Pupil Core detector.

        Args:
            image_path: Path to the image file.

        Returns:
            Tuple containing ellipse parameters dict and list of points on the ellipse.

        """
        # Load the image
        image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError(f"Failed to load image from path: {image_path}")

        result = self.detector.detect(image)
        ellipse = result["ellipse"]

        # Generate 5 points on the ellipse
        center = np.array(ellipse["center"])
        axes = np.array(ellipse["axes"]) / 2
        angle = np.deg2rad(ellipse["angle"])

        t = np.linspace(0, 2 * np.pi, 5, endpoint=False)
        points = np.column_stack([axes[0] * np.cos(t), axes[1] * np.sin(t)])
        rotation_matrix = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        points = np.dot(points, rotation_matrix.T) + center

        return ellipse, points.tolist()

    @property
    def name(self) -> str:
        """Get the name of the detector plugin."""
        return "Pupil Core"
