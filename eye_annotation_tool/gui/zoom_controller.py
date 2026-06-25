"""Zoom factor state and viewport-fit helper for the image viewer.

Owns the current zoom factor, its clamp range, and the first-image "already fit?"
lifecycle. The image viewer drives the repaint and the scroll anchoring; this keeps
the factor arithmetic in one place.
"""

from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QScrollArea


class ZoomController:
    """Zoom factor state machine for the image canvas."""

    MIN_FACTOR: float = 0.1
    MAX_FACTOR: float = 25.0
    FIT_MAX: float = 1.0
    ZOOM_STEP: float = 1.1

    def __init__(self) -> None:
        """Start unzoomed and unfit; the first ``load_image`` will fit + mark initialized."""
        self.factor: float = 1.0
        self._initialized: bool = False
        self.at_fit: bool = False  # True while showing fit-to-viewport zoom; refit on resize

    # ---------------------------------------------------------------------------
    # First-image fit lifecycle
    # ---------------------------------------------------------------------------

    def is_initialized(self) -> bool:
        """True once the first image has been fit to the viewport."""
        return self._initialized

    def mark_initialized(self) -> None:
        """Record that the inaugural fit has happened; subsequent images keep the user's zoom."""
        self._initialized = True

    # ---------------------------------------------------------------------------
    # Zoom actions
    # ---------------------------------------------------------------------------

    def set_factor(self, factor: float) -> bool:
        """Set the absolute zoom factor, clamped to the range. Return True if it changed.

        Only the factor state is mutated; the caller repaints and adjusts the scroll.
        """
        clamped = self._clamp(float(factor))
        if clamped == self.factor:
            return False
        self.at_fit = False
        self.factor = clamped
        return True

    def fit_to_viewport(self, scroll_area: QScrollArea, pixmap: QPixmap | None) -> None:
        """Pick the largest factor that lets ``pixmap`` fit inside the scroll viewport.

        Never enlarges (caps at ``FIT_MAX = 1.0``) — smaller images
        keep their 1x size instead of being scaled up to fill.
        """
        self.at_fit = True
        if pixmap is None or pixmap.isNull():
            return
        viewport = scroll_area.viewport().size()
        img_w, img_h = pixmap.width(), pixmap.height()
        if viewport.width() <= 0 or viewport.height() <= 0 or img_w == 0 or img_h == 0:
            self.factor = 1.0
            return
        fit = min(viewport.width() / img_w, viewport.height() / img_h, self.FIT_MAX)
        self.factor = max(self.MIN_FACTOR, fit)

    # ---------------------------------------------------------------------------
    # Internal
    # ---------------------------------------------------------------------------

    def _clamp(self, factor: float) -> float:
        """Constrain ``factor`` to ``[MIN_FACTOR, MAX_FACTOR]``."""
        return max(self.MIN_FACTOR, min(self.MAX_FACTOR, factor))
