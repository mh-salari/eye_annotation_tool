"""Placeholder eyelid detector for testing purposes."""

from ai.plugin_interface import DetectorPlugin


class PlaceholderEyelidDetector(DetectorPlugin):
    """Placeholder eyelid detector that returns dummy contour points."""

    def __init__(self) -> None:
        """Initialize the PlaceholderEyelidDetector."""

    def detect(self, image_path: str) -> list[tuple[float, float]]:  # noqa: PLR6301 ARG002
        """Detect eyelid contour in the given image (returns placeholder data).

        Args:
            image_path: Path to the image file.

        Returns:
            List of points representing the eyelid contour.

        """
        # For this example, we'll ignore the actual image content
        # and just return 4 fixed points representing an eyelid contour

        # Assuming image size of 192x192 (same as in the iris example)
        height, width = 192, 192

        # Define 4 points for the eyelid contour
        # These points form a simple curve across the top of the "eye"
        points = [
            (width * 0.2, height * 0.3),  # Left point
            (width * 0.4, height * 0.2),  # Left-middle point
            (width * 0.6, height * 0.2),  # Right-middle point
            (width * 0.8, height * 0.3),  # Right point
        ]

        return points

    @property
    def name(self) -> str:
        """Get the name of the detector plugin."""
        return "test"
