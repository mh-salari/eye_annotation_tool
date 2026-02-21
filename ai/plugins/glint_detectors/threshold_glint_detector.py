"""Threshold-based glint detector for bright spot detection in ROI."""

import cv2

from ai.plugin_interface import DetectorPlugin


class ThresholdGlintDetector(DetectorPlugin):
    """Glint detector using simple thresholding to find bright spots."""

    def __init__(self, roi: tuple | None = None) -> None:
        """Initialize the ThresholdGlintDetector.

        Args:
            roi: Optional ROI as (x, y, width, height) to limit detection area.

        """
        self.roi = roi

    def detect(self, image_path: str) -> list[tuple[float, float]]:
        """Detect glints in the given image using thresholding.

        Args:
            image_path: Path to the image file.

        Returns:
            List of points representing glint centers (x, y).

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

        # Use high threshold to find very bright regions
        # Otsu's method for bright regions
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Find contours of bright regions
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            raise ValueError("No bright regions found in the image")

        # Find centers of all bright spots
        glint_points = []
        for contour in contours:
            # Calculate moments to find center
            m = cv2.moments(contour)
            if m["m00"] > 0:  # Avoid division by zero
                cx = m["m10"] / m["m00"]
                cy = m["m01"] / m["m00"]

                # Adjust coordinates back to full image space if ROI was used
                cx += x
                cy += y

                glint_points.append((float(cx), float(cy)))

        if not glint_points:
            raise ValueError("No valid glint points found")

        return glint_points

    @property
    def name(self) -> str:
        """Get the name of the detector plugin."""
        return "Threshold"
