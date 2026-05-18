"""Zoom factor + viewport-fit helper for the image viewer.

The controller owns the current zoom factor and the first-image
"already fit?" flag. Wheel / button / fit actions go through it so
the clamp range and the keep-point-under-cursor scroll adjustment
live in one place.
"""

from PyQt5.QtCore import QPoint, QPointF
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QScrollArea, QWidget


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

    def zoom(self, zoom_in: bool, anchor_pos: QPoint, scroll_area: QScrollArea, widget: QWidget) -> None:
        """Step the zoom factor and adjust the scroll bars to keep ``anchor_pos`` fixed.

        ``anchor_pos`` is the cursor position in ``widget``'s local
        coordinates. ``widget`` is needed only to convert global ↔
        local coordinates for the scroll adjustment math.
        """
        old_factor = self.factor
        self.factor = self._clamp(old_factor * self.ZOOM_STEP if zoom_in else old_factor / self.ZOOM_STEP)
        if self.factor == old_factor:
            return
        viewport_center = scroll_area.viewport().rect().center()
        scene_pos = scroll_area.mapToGlobal(viewport_center) - widget.mapToGlobal(QPoint(0, 0))
        delta = anchor_pos - scene_pos
        ratio = self.factor / old_factor - 1
        h_bar = scroll_area.horizontalScrollBar()
        v_bar = scroll_area.verticalScrollBar()
        h_bar.setValue(int(h_bar.value() + delta.x() * ratio))
        v_bar.setValue(int(v_bar.value() + delta.y() * ratio))

    def zoom_in_centered(self, scroll_area: QScrollArea, widget: QWidget) -> None:
        """Zoom in around the centre of the visible viewport."""
        self.zoom(True, scroll_area.viewport().rect().center(), scroll_area, widget)

    def zoom_out_centered(self, scroll_area: QScrollArea, widget: QWidget) -> None:
        """Zoom out around the centre of the visible viewport."""
        self.zoom(False, scroll_area.viewport().rect().center(), scroll_area, widget)

    def fit_to_viewport(self, scroll_area: QScrollArea, pixmap: QPixmap | None) -> None:
        """Pick the largest factor that lets ``pixmap`` fit inside the scroll viewport.

        Never enlarges (caps at ``FIT_MAX = 1.0``) — smaller images
        keep their 1x size instead of being scaled up to fill.
        """
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

    # ---------------------------------------------------------------------------
    # Convenience for callers that pre-scale by ``factor``
    # ---------------------------------------------------------------------------

    def scaled(self, point: QPointF) -> QPointF:
        """Return ``point`` multiplied by the current zoom factor."""
        return QPointF(point.x() * self.factor, point.y() * self.factor)
