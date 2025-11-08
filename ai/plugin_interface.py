"""Abstract base class for detector plugins."""

from abc import ABC, abstractmethod


class DetectorPlugin(ABC):
    """Base class for all detector plugins (pupil, iris, eyelid)."""

    @abstractmethod
    def __init__(self) -> None:
        """Initialize the detector plugin."""

    @abstractmethod
    def detect(self, image: str) -> tuple | list:
        """Detect features in the given image.

        Args:
            image: Path to the image file.

        Returns:
            Detection results as tuple or list depending on detector type.

        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Get the name of the detector plugin."""
