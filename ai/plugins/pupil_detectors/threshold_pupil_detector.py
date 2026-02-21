"""Threshold-based pupil detector for dark pupil detection in ROI."""

import cv2
import numpy as np

from ai.plugin_interface import DetectorPlugin


class ThresholdPupilDetector(DetectorPlugin):
    """Pupil detector using simple thresholding to find dark regions."""

    def __init__(self, roi: tuple | None = None) -> None:
        """Initialize the ThresholdPupilDetector.

        Args:
            roi: Optional ROI as (x, y, width, height) to limit detection area.

        """
        self.roi = roi

    def detect(self, image_path: str) -> tuple[dict, list]:
        """Detect pupil in the given image using thresholding.

        Args:
            image_path: Path to the image file.

        Returns:
            Tuple containing ellipse parameters dict and list of points on the ellipse.

        """
        # Load the image
        image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError(f"Failed to load image from path: {image_path}")

        # Apply ROI if provided
        if self.roi:
            x, y, w, h = self.roi
            x, y, w, h = int(x), int(y), int(w), int(h)
            roi_image = image[y : y + h, x : x + w]
        else:
            roi_image = image
            x, y = 0, 0

        # Apply Gaussian blur to reduce noise
        blurred = cv2.GaussianBlur(roi_image, (5, 5), 0)

        # Use Otsu's thresholding to find dark regions
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Find contours
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            raise ValueError("No dark regions found in the image")

        # Find the largest contour (likely the pupil)
        largest_contour = max(contours, key=cv2.contourArea)

        # Need at least 5 points to fit an ellipse
        if len(largest_contour) < 5:
            raise ValueError("Not enough points to fit an ellipse")

        # Fit ellipse to the contour
        ellipse_params = cv2.fitEllipse(largest_contour)
        (cx, cy), (width, height), angle = ellipse_params

        # Adjust coordinates back to full image space if ROI was used
        cx += x
        cy += y

        # Generate 5 points on the ellipse
        t = np.linspace(0, 2 * np.pi, 5, endpoint=False)
        a, b = width / 2, height / 2
        angle_rad = np.deg2rad(angle)

        points = np.column_stack([a * np.cos(t), b * np.sin(t)])
        rotation_matrix = np.array([[np.cos(angle_rad), -np.sin(angle_rad)], [np.sin(angle_rad), np.cos(angle_rad)]])
        points = np.dot(points, rotation_matrix.T) + np.array([cx, cy])

        ellipse = {
            "center": (float(cx), float(cy)),
            "axes": (float(width), float(height)),
            "angle": float(angle),
        }

        return ellipse, points.tolist()

    @property
    def name(self) -> str:
        """Get the name of the detector plugin."""
        return "Threshold"
