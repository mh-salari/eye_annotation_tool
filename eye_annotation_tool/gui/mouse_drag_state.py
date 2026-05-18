"""Mouse-drag state machine fields for the image viewer.

The dispatch logic (``mousePressEvent`` / ``mouseMoveEvent`` /
``mouseReleaseEvent``) stays on :class:`ImageViewer` — input events
arrive on the widget and touch widget-level concerns (cursor shape,
signal emission, scroll bars, focus). What *is* a coherent unit are
the flag + coordinate fields the dispatch reads and writes across
the three event handlers; this dataclass groups them so the widget
exposes one ``self.mouse_state`` instead of thirteen mutable
attributes.
"""

from dataclasses import dataclass

from PyQt5.QtCore import QPoint, QPointF


@dataclass
class MouseDragState:
    """Mutable flags + coordinates spanning the three mouse handlers."""

    # Middle-button panning ----------------------------------------------------
    panning: bool = False
    last_pan_pos: QPoint | None = None

    # Manual-point selection / move (left-button in Manual mode) ---------------
    selected_point: QPointF | None = None
    moving_point: bool = False
    moving_all_points: bool = False
    last_mouse_pos: QPointF | None = None
    shift_pressed: bool = False

    # Per-target ROI drag-edit (left-button in Auto Detect mode) ---------------
    drawing_roi: bool = False
    moving_roi: bool = False
    resizing_roi: bool = False
    roi_resize_handle: str | None = None
    roi_start_pos: QPointF | None = None
    drawing_roi_committed: bool = False

    # Binocular divider drag ---------------------------------------------------
    divider_drag_active: bool = False

    # ---------------------------------------------------------------------------
    # Group-level transitions used by mouseReleaseEvent
    # ---------------------------------------------------------------------------

    def reset_roi_drag(self) -> None:
        """Clear every ROI-drag-mode flag (called on mouse release / target swap)."""
        self.drawing_roi = False
        self.moving_roi = False
        self.resizing_roi = False
        self.roi_resize_handle = None
        self.drawing_roi_committed = False

    def is_dragging_roi(self) -> bool:
        """True when any of the ROI drag-mode flags is set."""
        return self.drawing_roi or self.moving_roi or self.resizing_roi
