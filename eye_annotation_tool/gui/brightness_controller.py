"""Display-only brightness adjustment for the image viewer.

The original pixmap is never mutated — detector plugins keep seeing
the source-of-truth grayscale. This controller maintains a cached
adjusted pixmap, rebuilt only when the factor or source numpy array
changes; ``display_pixmap`` returns it (or the original when the
factor is identity).
"""

import math

import numpy as np
from PyQt5.QtGui import QImage, QPixmap


class BrightnessController:
    """Brightness multiplier + cached adjusted-pixmap for the canvas.

    The controller is purely about the display layer. The numpy
    grayscale ``rebuild()`` was last given is treated as the
    source-of-truth; passing a new array (e.g. on image load) triggers
    a refresh.
    """

    def __init__(self, step: float = 1.2, min_factor: float = 0.1, max_factor: float = 10.0) -> None:
        """Initialise the controller at factor 1.0 with the given step / clamp range."""
        self._step: float = step
        self._min: float = min_factor
        self._max: float = max_factor
        self.factor: float = 1.0
        self._cached: QPixmap | None = None

    def brighten(self) -> bool:
        """Multiply the factor by ``step``; returns whether the factor changed."""
        return self.set_factor(self.factor * self._step)

    def darken(self) -> bool:
        """Divide the factor by ``step``; returns whether the factor changed."""
        return self.set_factor(self.factor / self._step)

    def reset(self) -> bool:
        """Restore the factor to 1.0; returns whether the factor changed."""
        return self.set_factor(1.0)

    def set_factor(self, factor: float) -> bool:
        """Clamp ``factor`` and store; returns whether the value changed."""
        clamped = max(self._min, min(self._max, float(factor)))
        if clamped == self.factor and self._cached is not None:
            return False
        self.factor = clamped
        return True

    def rebuild(self, original_pixmap: QPixmap | None, grayscale: np.ndarray | None) -> None:
        """Recompute the cached pixmap from ``grayscale`` (or use ``original_pixmap`` at identity).

        Called by the widget on image load and on every factor change.
        Stores the result so :meth:`display_pixmap` can return it
        without recomputing on every repaint.
        """
        if original_pixmap is None or original_pixmap.isNull():
            self._cached = None
            return
        if grayscale is None or math.isclose(self.factor, 1.0, abs_tol=1e-6):
            self._cached = original_pixmap
            return
        adjusted = np.clip(grayscale.astype(np.float32) * self.factor, 0, 255).astype(np.uint8)
        h, w = adjusted.shape[:2]
        qimg = QImage(adjusted.tobytes(), w, h, w, QImage.Format_Grayscale8)
        self._cached = QPixmap.fromImage(qimg)

    def display_pixmap(self, original_pixmap: QPixmap | None) -> QPixmap | None:
        """Return the brightness-adjusted pixmap, falling back to ``original_pixmap``."""
        return self._cached if self._cached is not None else original_pixmap
