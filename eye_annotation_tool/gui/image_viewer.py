"""Image viewer widget for displaying and annotating eye images."""

import math
from collections import deque
from operator import itemgetter
from typing import ClassVar

import cv2
import numpy as np
from PyQt5.QtCore import QEvent, QPoint, QPointF, QSizeF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QKeyEvent, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QLabel, QMessageBox, QScrollArea, QVBoxLayout, QWidget

from ..utils.image_processing import find_closest_point, fit_ellipse

# Display brightness step factor per Brighter / Darker click, and the
# clamp range applied around the unmodified 1.0. 1.2 ~= +20 % per step
# (15 steps spans roughly 0.16x .. 6.2x). The brightening multiplies the
# numpy grayscale values and clips to [0, 255]; the source pixmap is
# never modified, so detector plugins keep seeing the original image.
BRIGHTNESS_STEP = 1.2
BRIGHTNESS_MIN = 0.1
BRIGHTNESS_MAX = 10.0


class ImageViewer(QWidget):
    """Widget for viewing and annotating eye images with pupil, limbus, eyelid, and glint markers."""

    # Maps a plugin target slug (used by the project settings file and the
    # auto-detector orchestrator) to the matching manual annotation slug
    # (used by ``current_annotation`` and the eye_data dict fields).
    _PLUGIN_TARGET_TO_ANNOTATION: ClassVar[dict[str, str]] = {
        "pupil": "pupil",
        "limbus": "limbus",
        "eyelid": "eyelid_contour",
        "glint": "glint",
    }

    annotation_changed = pyqtSignal()
    annotation_type_changed = pyqtSignal(str)
    # Emitted after a new image is loaded; the orchestrator listens to clear
    # its per-image detection cache.
    image_loaded = pyqtSignal()
    # Emitted on drag-end when the user has drawn, moved, or resized a
    # per-target Auto Detect ROI on the canvas. Payload is the target slug
    # ("pupil", ...) and the new ``(x, y, w, h)`` tuple or ``None``.
    target_roi_changed = pyqtSignal(str, object)
    # Emitted when the user finishes dragging the binocular divider line.
    # Payload is the new normalised x position in [0, 1]. MainWindow
    # persists the value as the current image's per-image override.
    divider_x_norm_changed = pyqtSignal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the ImageViewer."""
        super().__init__(parent)
        self.setup_ui()
        self.setup_variables()
        self.setup_undo_system()
        self.setup_colors()

        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_MouseTracking, True)
        self.setMouseTracking(True)

    def setup_ui(self) -> None:
        """Set up the user interface components."""
        layout = QVBoxLayout()
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.image_label)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.scroll_area)
        self.setLayout(layout)

        self.scroll_area.viewport().installEventFilter(self)

    def setup_variables(self) -> None:
        """Initialize instance variables."""
        self.factor = 1.0
        self.current_eye = "left"

        # Store annotations for both eyes separately
        self.eye_data = {
            "left": {
                "pupil_points": [],
                "limbus_points": [],
                "eyelid_contour_points": [],
                "glint_points": [],
                "pupil_ellipse": None,
                "limbus_ellipse": None,
            },
            "right": {
                "pupil_points": [],
                "limbus_points": [],
                "eyelid_contour_points": [],
                "glint_points": [],
                "pupil_ellipse": None,
                "limbus_ellipse": None,
            },
        }

        # Current working data (references to current eye data)
        self.pupil_points = []
        self.limbus_points = []
        self.eyelid_contour_points = []
        self.glint_points = []
        self.pupil_ellipse = None
        self.limbus_ellipse = None

        self.current_annotation = "pupil"
        self.original_pixmap = None
        self.selected_point = None
        self.moving_point = False
        self.panning = False
        self.last_pan_pos = None
        self.pixmap = None

        # Display brightness. ``1.0`` = unmodified (the canvas paints the
        # original pixels). Brighter / darker buttons step this by
        # ``BRIGHTNESS_STEP`` and rebuild ``_brightness_pixmap`` from the
        # numpy grayscale view so the source-of-truth ``original_pixmap``
        # stays untouched and detector plugins keep seeing the unmodified
        # image.
        self._display_brightness = 1.0
        self._brightness_pixmap: QPixmap | None = None

        # Batch-update gate. Setters call ``update_image()`` to repaint after
        # mutating state; on heavy load paths (apply_loaded_detections,
        # live-plugin refresh for both eyes) those calls stack up into many
        # full-canvas paints. ``_updates_paused`` collapses them: while True,
        # ``update_image`` marks ``_update_pending`` and returns; the
        # matching ``resume_updates`` issues a single repaint if any was
        # requested. Depth-counted so nested pause/resume composes safely.
        self._updates_paused = 0
        self._update_pending = False
        # Variables for shift-click point movement
        self.shift_pressed = False
        self.last_mouse_pos = None
        self.moving_all_points = False

        # Per-target ROI drag state. Used by both mouse-event paths for
        # any plugin whose panel exposes roi_edit_requested. The
        # ``drawing_roi_committed`` flag flips True only once the user
        # has dragged past :attr:`ROI_DRAG_THRESHOLD_PX` since press —
        # below that the previous rectangle is preserved and the
        # release path skips re-emitting, so a stray click doesn't
        # clear a valid ROI.
        self.drawing_roi = False
        self.roi_start_pos = None
        self.moving_roi = False
        self.resizing_roi = False
        self.roi_resize_handle = None  # 'tl', 'tr', 'bl', 'br' for corners
        self._drawing_roi_committed = False

        # Binocular mode: True when the image contains two eyes split by
        # a vertical divider. False = monocular (single eye fills the
        # image, ``current_eye`` is pinned to "left", divider + right
        # block are never drawn).
        self.binocular_mode = True

        # Vertical divider position as a fraction of image width in
        # ``[0, 1]``. Only used when ``binocular_mode`` is True; defines
        # which half of the image counts as the left eye vs the right
        # eye for click gating, dim-overlay rendering, and auto-detector
        # cropping. ``divider_drag_active`` flags the in-progress drag.
        self._divider_x_norm = 0.5
        self._divider_drag_active = False

        # Grayscale numpy view of the currently loaded image, kept alongside
        # the Qt pixmap so detector plugins can consume it without re-reading
        # from disk.
        self.image_grayscale: np.ndarray | None = None

        # Per-eye, per-target Auto Detect overlay results. Outer key is
        # the anatomical target ("pupil" / "glint" / "limbus" / "eyelid"),
        # inner key the eye slot ("left" / "right" / "single"). Both
        # eyes' overlays paint at the same time so the user can see at
        # a glance which halves they've already processed; the dim wash
        # over the inactive half indicates which side they're currently
        # working on. The inner dict's value shape matches the
        # corresponding plugin's serialise/deserialise contract.
        self._detection_overlays: dict[str, dict[str, dict]] = {}

        # Plugin instance currently owning each target. Populated by
        # MainWindow when panels are mounted (and cleared when a target's
        # plugin is set to "disabled"). The viewer reads each plugin's
        # ``draw_overlay`` method + ``roi_color`` / ``mask_color`` attrs
        # so adding a new plugin needs no edits here.
        self._active_plugins: dict[str, object] = {}

        # Plugin targets currently owned by an auto detector. Manual
        # painting and click-to-place for these targets is suppressed so
        # each target has a single source of truth (auto OR manual,
        # never both). Carries plugin-target slugs ("pupil"/"glint"/
        # "limbus"/"eyelid"); the painter and click paths translate to
        # the annotation-slug naming via ``_PLUGIN_TARGET_TO_ANNOTATION``.
        self._auto_managed_targets: set[str] = set()
        self._auto_managed_annotations: set[str] = set()

        # Gate for manual click-to-place and click-to-edit on the
        # canvas. True only in Manual mode — Auto Detect mode keeps
        # manual annotations visible but blocks adding / dragging them
        # so the user has to switch back to Manual before touching them.
        # MainWindow flips this on mode change.
        self._manual_edit_enabled = True

        # Per-eye, per-target Auto Detect ROI rectangles. Outer key the
        # anatomical target, inner key the eye slot. ``_active_roi_target``
        # is the target whose rectangle is currently in drag-edit mode —
        # drag handles + canvas clicks only operate on the active eye's
        # rectangle for that target; the inactive eye's rectangle paints
        # without handles for context.
        self._target_rois: dict[str, dict[str, tuple]] = {}
        self._active_roi_target: str | None = None

        # Per-eye, per-target threshold-mask overlays. ``_target_masks``
        # holds the ``uint8`` ndarray a plugin's ``detect`` returned under
        # its ``"mask"`` key (or absent when the plugin didn't produce
        # one). ``_show_target_masks`` is per-target (not per-eye) — the
        # Show mask checkbox toggles every stored mask for that target.
        # All stores are populated from outside via the public setters;
        # the viewer never inspects plugin result shapes itself.
        self._target_masks: dict[str, dict[str, np.ndarray]] = {}
        self._show_target_masks: dict[str, bool] = {}

    def setup_colors(self) -> None:
        # Define colors with transparency
        """Set up color definitions for annotations."""
        self.pupil_color = QColor(150, 213, 116, 255)
        self.pupil_select_color = QColor(249, 248, 113, 255)
        self.pupil_ellipse_color = QColor(25, 145, 50, 255)
        # Brighter shade than the ellipse outline so the centre dot
        # reads on top of both the outline and the soft-green mask
        # fill the auto pupil plugin can paint underneath.
        self.pupil_center_color = QColor(180, 240, 80, 255)

        self.limbus_color = QColor(194, 149, 188, 255)
        self.limbus_select_color = QColor(249, 178, 208, 255)
        self.limbus_ellipse_color = QColor(139, 122, 162, 255)

        self.eyelid_color = QColor(0, 155, 201, 255)
        self.eyelid_select_color = QColor(0, 189, 194, 255)
        self.eyelid_ellipse_color = QColor(0, 118, 195, 255)

        self.glint_color = QColor(255, 165, 0, 255)  # Orange
        self.glint_select_color = QColor(255, 215, 0, 255)  # Gold

        # Fallback colours used when an Auto Detect ROI or threshold mask
        # belongs to a plugin that didn't declare its own colour. Plugins
        # are expected to set ``roi_color`` / ``mask_color`` on the class
        # body — these defaults exist only so a misconfigured plugin
        # still renders something visible instead of crashing.
        self._fallback_roi_color = QColor(255, 255, 255, 200)
        self._fallback_mask_color = QColor(255, 255, 255, 64)

        # Binocular divider line + dim overlay for the inactive eye.
        # Divider is bright white so it reads as a UI separator distinct
        # from every data layer (pupil teal / limbus purple / glint red
        # / mask cyan / mask magenta). The dim overlay is a low-alpha
        # black wash that darkens the inactive half without hiding the
        # eye entirely.
        self.divider_color = QColor(255, 255, 255, 230)
        self.inactive_eye_dim_color = QColor(0, 0, 0, 120)

    def setup_undo_system(self) -> None:
        """Initialize the undo/redo system."""
        self.undo_stack = deque(maxlen=10)
        self.undo_index = -1

    def save_current_eye_data(self) -> None:
        """Save the current working data back to the eye_data dictionary."""
        self.eye_data[self.current_eye]["pupil_points"] = self.pupil_points.copy()
        self.eye_data[self.current_eye]["limbus_points"] = self.limbus_points.copy()
        self.eye_data[self.current_eye]["eyelid_contour_points"] = self.eyelid_contour_points.copy()
        self.eye_data[self.current_eye]["glint_points"] = self.glint_points.copy()
        self.eye_data[self.current_eye]["pupil_ellipse"] = self.pupil_ellipse
        self.eye_data[self.current_eye]["limbus_ellipse"] = self.limbus_ellipse

    def load_current_eye_data(self) -> None:
        """Load the data for the current eye into working variables."""
        self.pupil_points = self.eye_data[self.current_eye]["pupil_points"].copy()
        self.limbus_points = self.eye_data[self.current_eye]["limbus_points"].copy()
        self.eyelid_contour_points = self.eye_data[self.current_eye]["eyelid_contour_points"].copy()
        self.glint_points = self.eye_data[self.current_eye]["glint_points"].copy()
        self.pupil_ellipse = self.eye_data[self.current_eye]["pupil_ellipse"]
        self.limbus_ellipse = self.eye_data[self.current_eye]["limbus_ellipse"]

    def switch_eye(self, eye: str) -> None:
        """Switch between left and right eye annotations."""
        if eye not in {"left", "right"}:
            return

        # In monocular mode the right block is unused; defend against
        # programmatic callers requesting "right" so saves stay in sync
        # with the left-only convention.
        if not self.binocular_mode and eye != "left":
            return

        self.save_current_eye_data()
        self.current_eye = eye
        self.load_current_eye_data()
        self.update_image()
        self.annotation_changed.emit()

    def set_binocular_mode(self, enabled: bool) -> None:
        """Toggle binocular mode and re-render.

        When flipping to monocular the active eye is forced back to
        "left" so the in-memory store + on-disk save stay aligned with
        the flat monocular schema.
        """
        self.binocular_mode = enabled
        if not enabled and self.current_eye != "left":
            self.save_current_eye_data()
            self.current_eye = "left"
            self.load_current_eye_data()
        self.update_image()

    def set_divider_x_norm(self, value: float) -> None:
        """Set the binocular divider position as a fraction of image width."""
        clamped = max(0.0, min(1.0, float(value)))
        if clamped == self._divider_x_norm:
            return
        self._divider_x_norm = clamped
        self.update_image()

    def get_divider_x_norm(self) -> float:
        """Return the current normalised divider position."""
        return self._divider_x_norm

    def _divider_x_image(self) -> float:
        """Return the divider position in image coordinates (pixels)."""
        if self.original_pixmap is None or self.original_pixmap.isNull():
            return 0.0
        return self._divider_x_norm * self.original_pixmap.width()

    def _point_on_active_side(self, point: QPointF) -> bool:
        """Return True when ``point`` falls on the currently selected eye's side."""
        if not self.binocular_mode:
            return True
        divider = self._divider_x_image()
        if self.current_eye == "left":
            return point.x() < divider
        return point.x() >= divider

    def get_all_eye_data(self) -> dict:
        """Get annotation data for both eyes."""
        # Save current working data first
        self.save_current_eye_data()
        return self.eye_data.copy()

    def set_all_eye_data(self, eye_data: dict) -> None:
        """Set annotation data for both eyes."""
        self.eye_data = eye_data.copy()
        self.load_current_eye_data()
        self.update_image()

    def reset_undo_stack(self, initial_state: dict | None = None) -> None:
        """Reset the undo stack to initial state."""
        self.undo_stack.clear()
        self.undo_index = -1
        if initial_state is None:
            initial_state = self.get_current_state()
        self.undo_stack.append(initial_state)
        self.undo_index = 0

    def get_current_state(self) -> dict:
        """Get the current state of all annotations."""
        return {
            "pupil_points": self.pupil_points.copy(),
            "limbus_points": self.limbus_points.copy(),
            "eyelid_contour_points": self.eyelid_contour_points.copy(),
            "glint_points": self.glint_points.copy(),
            "pupil_ellipse": self.pupil_ellipse,
            "limbus_ellipse": self.limbus_ellipse,
        }

    def save_state(self) -> None:
        """Save the current state to the undo stack."""
        state = self.get_current_state()
        if self.undo_index < len(self.undo_stack) - 1:
            # If we're not at the end of the stack, remove future states
            self.undo_stack = deque(list(self.undo_stack)[: self.undo_index + 1], maxlen=5)
        self.undo_stack.append(state)
        self.undo_index = len(self.undo_stack) - 1

    def can_undo(self) -> bool:
        """Check if undo operation is available."""
        return self.undo_index > 0

    def undo(self) -> None:
        """Undo the last annotation change."""
        if self.can_undo():
            self.undo_index -= 1
            state = self.undo_stack[self.undo_index]
            self.pupil_points = state["pupil_points"].copy()
            self.limbus_points = state["limbus_points"].copy()
            self.eyelid_contour_points = state.get("eyelid_contour_points", []).copy()
            self.glint_points = state.get("glint_points", []).copy()
            self.pupil_ellipse = state["pupil_ellipse"]
            self.limbus_ellipse = state["limbus_ellipse"]
            self.save_current_eye_data()
            self.update_image()
            self.annotation_changed.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Handle key press events."""
        if event.key() == Qt.Key_Plus or event.key() == Qt.Key_Equal:
            self.zoom(True, self.rect().center())
        elif event.key() == Qt.Key_Minus:
            self.zoom(False, self.rect().center())
        elif event.key() == Qt.Key_Delete:
            self.delete_selected_point()
        elif event.key() == Qt.Key_Shift:
            self.shift_pressed = True
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Handle key release events."""
        if event.key() == Qt.Key_Shift:
            self.shift_pressed = False
        super().keyReleaseEvent(event)

    def delete_selected_point(self) -> None:
        """Delete the currently selected point."""
        if self.selected_point:
            if self.current_annotation == "pupil":
                points = self.pupil_points
            elif self.current_annotation == "limbus":
                points = self.limbus_points
            elif self.current_annotation == "eyelid_contour":
                points = self.eyelid_contour_points
            else:  # glint
                points = self.glint_points

            if self.selected_point in points:
                points.remove(self.selected_point)
                self.selected_point = None
                self.save_state()
                self.save_current_eye_data()
                self.annotation_changed.emit()
                self.update_image()

    def _active_drag_roi(self) -> tuple | None:
        """Return the rectangle currently being drag-edited on the active eye (or None)."""
        if self._active_roi_target is None:
            return None
        return self._target_rois.get(self._active_roi_target, {}).get(self.active_eye_slot())

    def _set_active_drag_roi(self, value: tuple | None) -> None:
        """Write the in-progress drag rectangle to the active eye's slot for the active target."""
        if self._active_roi_target is None:
            return
        slot = self.active_eye_slot()
        if value is None:
            self._drop_slot(self._target_rois, self._active_roi_target, slot)
        else:
            self._target_rois.setdefault(self._active_roi_target, {})[slot] = value

    # Minimum cursor movement (image coords) before a press counts as a
    # drag-to-draw rather than a click. Below this, the rectangle stays
    # uncommitted so an accidental click on an active ROI target does
    # not produce an invisible / one-pixel rectangle.
    ROI_DRAG_THRESHOLD_PX = 5.0

    def _drag_active_roi(self, new_pos: QPointF) -> None:
        """Update the active drag ROI in place based on the current cursor position.

        Dispatches by ``drawing_roi`` / ``moving_roi`` / ``resizing_roi``
        flags. The active rectangle is read via :meth:`_active_drag_roi`
        and written back via :meth:`_set_active_drag_roi`.
        """
        if self.drawing_roi:
            dx = new_pos.x() - self.roi_start_pos.x()
            dy = new_pos.y() - self.roi_start_pos.y()
            if max(abs(dx), abs(dy)) < self.ROI_DRAG_THRESHOLD_PX:
                return
            x = min(self.roi_start_pos.x(), new_pos.x())
            y = min(self.roi_start_pos.y(), new_pos.y())
            w = abs(dx)
            h = abs(dy)
            self._set_active_drag_roi((x, y, w, h))
            self._drawing_roi_committed = True
            return
        current = self._active_drag_roi()
        if current is None:
            return
        if self.moving_roi:
            delta_x = new_pos.x() - self.roi_start_pos.x()
            delta_y = new_pos.y() - self.roi_start_pos.y()
            x, y, w, h = current
            self._set_active_drag_roi((x + delta_x, y + delta_y, w, h))
            self.roi_start_pos = new_pos
            return
        if self.resizing_roi:
            x, y, w, h = current
            if "t" in self.roi_resize_handle:
                delta_y = new_pos.y() - y
                y = new_pos.y()
                h -= delta_y
            if "b" in self.roi_resize_handle:
                h = new_pos.y() - y
            if "l" in self.roi_resize_handle:
                delta_x = new_pos.x() - x
                x = new_pos.x()
                w -= delta_x
            if "r" in self.roi_resize_handle:
                w = new_pos.x() - x
            self._set_active_drag_roi((x, y, max(10, w), max(10, h)))

    def _try_begin_roi_drag(self, image_pos: QPointF, current: tuple | None) -> bool:
        """Convert a click on or near ``current`` into a resize/move/draw state.

        Returns True iff this click consumes the event (i.e. it landed on
        an ROI handle / inside the rectangle, or it began a fresh draw).
        Always returns True under the present caller — the caller has
        already established that some ROI drag mode is active.
        """
        if current:
            handle = self.get_roi_handle_at_pos(image_pos, current)
            if handle:
                self.resizing_roi = True
                self.roi_resize_handle = handle
                self.roi_start_pos = image_pos
                return True
            if self.is_point_in_roi(image_pos, current):
                self.moving_roi = True
                self.roi_start_pos = image_pos
                return True
        # Begin a fresh draw, but keep the previous rectangle in place
        # until the user actually drags past the threshold — a stray
        # click without movement leaves the existing ROI untouched.
        self.drawing_roi = True
        self._drawing_roi_committed = False
        self.roi_start_pos = image_pos
        return True

    # Half-width of the hot zone for grabbing the binocular divider, in
    # image coordinates. 6px reads as "wide enough to grab" without
    # intercepting clicks that are clearly on the eye.
    DIVIDER_GRAB_HALF_WIDTH = 6.0

    def _hit_divider(self, image_pos: QPointF) -> bool:
        """Return True when ``image_pos`` falls inside the divider grab zone."""
        if not self.binocular_mode:
            return False
        divider = self._divider_x_image()
        return abs(image_pos.x() - divider) <= self.DIVIDER_GRAB_HALF_WIDTH

    def mousePressEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle mouse press events."""
        if event.button() == Qt.MiddleButton:
            self.panning = True
            self.last_pan_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.LeftButton:
            image_pos = self.get_image_position(event.pos())
            if image_pos:
                # The binocular divider grabs the click before anything
                # else so the user can always retarget the line even when
                # it overlaps an ROI handle or an annotation point.
                if self._hit_divider(image_pos):
                    self._divider_drag_active = True
                    self.setCursor(Qt.SizeHorCursor)
                    return

                # A per-target ROI in drag-edit mode consumes the click
                # before any Manual-mode click-to-place flow runs. Drag
                # operates on the active eye's slot for that target —
                # the inactive eye's rectangle paints but isn't grabbable.
                if self._active_roi_target is not None:
                    self._try_begin_roi_drag(image_pos, self._active_drag_roi())
                    return

                # In Auto Detect mode manual edits are off — clicks on
                # the canvas that don't hit the divider or an active
                # ROI handle do nothing. The user must switch back to
                # Manual mode to add or move manual annotations.
                if not self._manual_edit_enabled:
                    return

                self.selected_point, selected_annotation = self.find_closest_point_and_type(image_pos)

                if self.selected_point:
                    self.moving_point = True
                    self.last_mouse_pos = image_pos
                    self.moving_all_points = self.shift_pressed

                    if selected_annotation != self.current_annotation:
                        self.current_annotation = selected_annotation
                        self.annotation_type_changed.emit(self.current_annotation)
                elif self.current_annotation in self._auto_managed_annotations:
                    # The current target is owned by an auto detector; manual
                    # click-to-place is disabled so the auto overlay stays the
                    # single source of truth for this target.
                    return
                elif not self._point_on_active_side(image_pos):
                    # In binocular mode clicks on the inactive eye's half
                    # are ignored — the user should switch eyes first
                    # rather than accidentally annotate the wrong side.
                    return
                elif self.current_annotation == "pupil":
                    self.pupil_points.append(image_pos)
                elif self.current_annotation == "limbus":
                    self.limbus_points.append(image_pos)
                elif self.current_annotation == "eyelid_contour":
                    self.eyelid_contour_points.append(image_pos)
                else:  # glint
                    self.glint_points.append(image_pos)

                self.save_state()
                self.save_current_eye_data()
                self.annotation_changed.emit()
                self.update_image()

    def mouseMoveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle mouse move events."""
        if self.panning:
            delta = event.pos() - self.last_pan_pos
            self.scroll_area.horizontalScrollBar().setValue(self.scroll_area.horizontalScrollBar().value() - delta.x())
            self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().value() - delta.y())
            self.last_pan_pos = event.pos()
        elif self._divider_drag_active:
            new_pos = self.get_image_position(event.pos())
            if new_pos is not None and self.original_pixmap is not None:
                self.set_divider_x_norm(new_pos.x() / self.original_pixmap.width())
        elif self.drawing_roi or self.moving_roi or self.resizing_roi:
            new_pos = self.get_image_position(event.pos())
            if new_pos and self.roi_start_pos:
                self._drag_active_roi(new_pos)
                self.update_image()
        elif self.moving_point and self.selected_point:
            new_pos = self.get_image_position(event.pos())
            if new_pos and self.last_mouse_pos:
                # Calculate the movement delta
                delta_x = new_pos.x() - self.last_mouse_pos.x()
                delta_y = new_pos.y() - self.last_mouse_pos.y()

                if self.moving_all_points:
                    # Move all points in the current annotation type
                    if self.current_annotation == "pupil":
                        self.move_points_by_delta(self.pupil_points, delta_x, delta_y)
                    elif self.current_annotation == "limbus":
                        self.move_points_by_delta(self.limbus_points, delta_x, delta_y)
                    elif self.current_annotation == "eyelid_contour":
                        self.move_points_by_delta(self.eyelid_contour_points, delta_x, delta_y)
                    else:  # glint
                        self.move_points_by_delta(self.glint_points, delta_x, delta_y)
                # Move only the selected point
                elif self.current_annotation == "pupil":
                    index = self.pupil_points.index(self.selected_point)
                    self.pupil_points[index] = new_pos
                elif self.current_annotation == "limbus":
                    index = self.limbus_points.index(self.selected_point)
                    self.limbus_points[index] = new_pos
                elif self.current_annotation == "eyelid_contour":
                    index = self.eyelid_contour_points.index(self.selected_point)
                    self.eyelid_contour_points[index] = new_pos
                else:  # glint
                    index = self.glint_points.index(self.selected_point)
                    self.glint_points[index] = new_pos

                self.selected_point = new_pos
                self.last_mouse_pos = new_pos
                self.save_current_eye_data()
                self.update_image()

    def mouseReleaseEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle mouse release events."""
        if event.button() == Qt.MiddleButton:
            self.panning = False
            self.setCursor(Qt.ArrowCursor)
        elif event.button() == Qt.LeftButton:
            if self._divider_drag_active:
                self._divider_drag_active = False
                self.setCursor(Qt.ArrowCursor)
                self.divider_x_norm_changed.emit(self._divider_x_norm)
                return
            if self.drawing_roi or self.moving_roi or self.resizing_roi:
                # A draw that never crossed the threshold is treated as
                # a stray click — leave the previous ROI alone and skip
                # the emit so the panel doesn't get re-notified with
                # stale data.
                was_drawing = self.drawing_roi
                committed = self._drawing_roi_committed
                self.drawing_roi = False
                self.moving_roi = False
                self.resizing_roi = False
                self.roi_resize_handle = None
                self._drawing_roi_committed = False
                draw_changed_roi = (not was_drawing) or committed
                if draw_changed_roi and self._active_roi_target is not None:
                    target = self._active_roi_target
                    self.target_roi_changed.emit(
                        target,
                        self._target_rois.get(target, {}).get(self.active_eye_slot()),
                    )
                return

            self.moving_point = False
            if self.selected_point:
                self.save_state()
                self.save_current_eye_data()
                self.annotation_changed.emit()

    def wheelEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle mouse wheel events for zooming."""
        if event.modifiers() == Qt.ControlModifier:
            zoom_in = event.angleDelta().y() > 0
            self.zoom(zoom_in, event.position().toPoint())
            event.accept()  # Prevent the event from being passed to the parent widget
        else:
            # Only allow scrolling when not zooming
            super().wheelEvent(event)

    def load_image(self, image_path: str) -> bool:
        """Load an image from the given path.

        Reads both the Qt pixmap (for display) and a grayscale numpy array
        for detector plugins to consume. Failing to decode the grayscale
        array is non-fatal — the pixmap path is the user-visible source of
        truth and the plugin-driven Auto Detect mode just stays inert.
        """
        self.original_pixmap = QPixmap(image_path)
        if self.original_pixmap.isNull():
            return False
        self.image_grayscale = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        # Display brightness carries forward across image navigation —
        # once the user has tuned the canvas to see darker / brighter
        # pixels in one frame, the next frame keeps the same factor so
        # they don't have to re-tune per image. Rebuild the cached
        # brightness pixmap against the freshly loaded grayscale.
        self._rebuild_brightness_pixmap()
        self.pupil_points = []
        self.limbus_points = []
        self.pupil_ellipse = None
        self.limbus_ellipse = None
        # Per-image Auto Detect overlay state — cleared here so the next
        # image starts blank before the annotation controller restores
        # whatever the new image's saved annotation carries. Masks are
        # transient (never persisted), so they always start empty on a
        # new image until the next plugin run.
        self._detection_overlays.clear()
        self._target_rois.clear()
        self._target_masks.clear()
        self.reset_undo_stack()
        self._fit_to_viewport()
        self.update_image()
        self.image_loaded.emit()
        return True

    def zoom_in_centered(self) -> None:
        """Zoom in around the centre of the visible viewport."""
        self.zoom(True, self.scroll_area.viewport().rect().center())

    def zoom_out_centered(self) -> None:
        """Zoom out around the centre of the visible viewport."""
        self.zoom(False, self.scroll_area.viewport().rect().center())

    def reset_zoom_to_fit(self) -> None:
        """Restore the zoom factor to fit the whole image inside the viewport."""
        self._fit_to_viewport()
        self.update_image()

    def brighten_display(self) -> None:
        """Multiply the displayed grayscale by ``BRIGHTNESS_STEP`` (clamped)."""
        self._set_display_brightness(self._display_brightness * BRIGHTNESS_STEP)

    def darken_display(self) -> None:
        """Divide the displayed grayscale by ``BRIGHTNESS_STEP`` (clamped)."""
        self._set_display_brightness(self._display_brightness / BRIGHTNESS_STEP)

    def reset_display_brightness(self) -> None:
        """Restore the displayed grayscale to its source values (factor = 1.0)."""
        self._set_display_brightness(1.0)

    def _set_display_brightness(self, factor: float) -> None:
        """Clamp ``factor`` to the brightness range, rebuild the display pixmap, repaint."""
        factor = max(BRIGHTNESS_MIN, min(BRIGHTNESS_MAX, float(factor)))
        if factor == self._display_brightness and self._brightness_pixmap is not None:
            return
        self._display_brightness = factor
        self._rebuild_brightness_pixmap()
        self.update_image()

    def _rebuild_brightness_pixmap(self) -> None:
        """Recompute the cached brightness-adjusted pixmap from the numpy grayscale.

        Identity case (factor == 1.0) reuses ``original_pixmap`` directly so
        the no-adjustment path costs nothing. Other factors multiply the
        ``image_grayscale`` array, clip to ``[0, 255]``, and wrap the result
        back into a QPixmap — kept as a class attribute so ``update_image``
        can scale from it without recomputing each repaint.
        """
        if self.original_pixmap is None or self.original_pixmap.isNull():
            self._brightness_pixmap = None
            return
        # Brighten/darken steps multiply by 1.2 so the value rarely lands
        # back on exactly 1.0 after a few cycles; treat anything within
        # ``1e-6`` of unity as identity to short-circuit the rebuild.
        if math.isclose(self._display_brightness, 1.0, abs_tol=1e-6) or self.image_grayscale is None:
            self._brightness_pixmap = self.original_pixmap
            return
        adjusted = np.clip(
            self.image_grayscale.astype(np.float32) * self._display_brightness,
            0,
            255,
        ).astype(np.uint8)
        h, w = adjusted.shape[:2]
        # Copy ensures the numpy buffer outlives the QImage view.
        qimg = QImage(adjusted.tobytes(), w, h, w, QImage.Format_Grayscale8)
        self._brightness_pixmap = QPixmap.fromImage(qimg)

    def _fit_to_viewport(self) -> None:
        """Pick a zoom factor so the loaded image fits inside the scroll viewport.

        Reset on every image load so a wide binocular image is fully
        visible without manual zoom-out, while smaller images don't get
        scaled up past 1x (we never enlarge — only shrink to fit).
        """
        if self.original_pixmap is None or self.original_pixmap.isNull():
            return
        viewport = self.scroll_area.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            self.factor = 1.0
            return
        img_w = self.original_pixmap.width()
        img_h = self.original_pixmap.height()
        if img_w == 0 or img_h == 0:
            self.factor = 1.0
            return
        fit_w = viewport.width() / img_w
        fit_h = viewport.height() / img_h
        self.factor = max(0.1, min(1.0, fit_w, fit_h))

    def get_current_image_grayscale(self) -> np.ndarray | None:
        """Return the grayscale numpy view of the current image (or None)."""
        return self.image_grayscale

    # ----- Auto Detect overlays -----

    def set_manual_edit_enabled(self, enabled: bool) -> None:
        """Allow / block manual click-to-place and click-to-edit on the canvas.

        Called by MainWindow when the user flips the mode switcher.
        Existing manual annotations stay visible regardless; only
        adding new ones and dragging existing ones is gated.
        """
        self._manual_edit_enabled = bool(enabled)

    def set_auto_managed_targets(self, plugin_targets: set[str]) -> None:
        """Declare which plugin targets are now owned by an auto detector.

        Manual annotations and click-to-place for these targets are
        suppressed; the auto-detector overlay is the only visible source
        for those targets. Targets not in the set fall back to the
        manual annotation path.
        """
        self._auto_managed_targets = set(plugin_targets)
        self._auto_managed_annotations = {
            self._PLUGIN_TARGET_TO_ANNOTATION[t]
            for t in self._auto_managed_targets
            if t in self._PLUGIN_TARGET_TO_ANNOTATION
        }
        self.update_image()

    def clear_manual_for_target(self, plugin_target: str) -> None:
        """Wipe manual points + fitted ellipse for ``plugin_target`` on both eyes.

        Called when an auto detector takes ownership of ``plugin_target``
        so the previously placed manual annotations don't linger in the
        eye_data store. The current undo state is pushed first so Ctrl-Z
        can restore the wiped points.
        """
        annotation = self._PLUGIN_TARGET_TO_ANNOTATION.get(plugin_target)
        if annotation is None:
            return
        field_map = {
            "pupil": ("pupil_points", "pupil_ellipse"),
            "limbus": ("limbus_points", "limbus_ellipse"),
            "eyelid_contour": ("eyelid_contour_points", None),
            "glint": ("glint_points", None),
        }
        points_field, ellipse_field = field_map[annotation]
        had_data = False
        for eye in ("left", "right"):
            if self.eye_data[eye][points_field]:
                had_data = True
                self.eye_data[eye][points_field] = []
            if ellipse_field and self.eye_data[eye][ellipse_field] is not None:
                had_data = True
                self.eye_data[eye][ellipse_field] = None
        if not had_data:
            return
        self.load_current_eye_data()
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def set_active_plugin(self, target: str, plugin: object) -> None:
        """Record the plugin instance currently owning ``target``.

        Called by MainWindow whenever a plugin panel is mounted so the
        viewer can later route detection overlay drawing through
        ``plugin.draw_overlay`` and pick up the plugin's own
        ``roi_color`` / ``mask_color`` palette.
        """
        self._active_plugins[target] = plugin

    def clear_active_plugin(self, target: str) -> None:
        """Drop the plugin reference for ``target`` (panel unmounted / disabled)."""
        self._active_plugins.pop(target, None)

    def active_eye_slot(self) -> str:
        """Return the per-eye storage key for the currently active eye."""
        return self.current_eye if self.binocular_mode else "single"

    def _resolve_slot(self, eye_slot: str | None) -> str:
        """Map ``None`` to the currently active eye slot."""
        return eye_slot if eye_slot is not None else self.active_eye_slot()

    @staticmethod
    def _drop_slot(store: dict, target: str, slot: str) -> bool:
        """Remove ``slot`` from ``store[target]``; prune empty target dicts. Returns whether anything was removed."""
        by_slot = store.get(target)
        if not by_slot or slot not in by_slot:
            return False
        del by_slot[slot]
        if not by_slot:
            del store[target]
        return True

    # ----- Per-eye, per-target Auto Detect overlays -----

    def set_detection_overlay(self, target: str, result: dict, *, eye_slot: str | None = None) -> None:
        """Store an Auto Detect result for ``target`` at ``eye_slot`` and re-paint.

        ``eye_slot`` defaults to the active eye; pass ``"left"`` /
        ``"right"`` / ``"single"`` explicitly when populating from a
        loaded annotation file that carries results for both eyes.
        """
        slot = self._resolve_slot(eye_slot)
        self._detection_overlays.setdefault(target, {})[slot] = result
        self.update_image()

    def clear_detection_overlay(self, target: str, *, eye_slot: str | None = None) -> None:
        """Drop the stored result for ``target`` at ``eye_slot`` (or every slot when ``eye_slot`` is None)."""
        if eye_slot is None:
            if self._detection_overlays.pop(target, None) is not None:
                self.update_image()
            return
        if self._drop_slot(self._detection_overlays, target, eye_slot):
            self.update_image()

    def clear_all_detection_overlays(self) -> None:
        """Drop every stored Auto Detect result and re-paint. Called on image change."""
        if self._detection_overlays:
            self._detection_overlays.clear()
            self.update_image()

    def get_detection_overlay(self, target: str, *, eye_slot: str | None = None) -> dict | None:
        """Return the result stored for ``(target, eye_slot)``, or None."""
        slot = self._resolve_slot(eye_slot)
        return self._detection_overlays.get(target, {}).get(slot)

    # ----- Per-eye, per-target Auto Detect ROIs -----

    def set_active_roi_target(self, target: str | None) -> None:
        """Enter drag-edit mode for ``target``'s ROI, or leave it (``None``).

        Cancels any in-progress drag and re-paints so the corner-handle
        decoration follows the newly active target.
        """
        if target is not None and target == self._active_roi_target:
            return
        self._active_roi_target = target
        # Cancel any drag in progress; the user toggled the active ROI
        # while pressing the mouse — rare but worth a clean reset.
        self.drawing_roi = False
        self.moving_roi = False
        self.resizing_roi = False
        self.roi_resize_handle = None
        self._drawing_roi_committed = False
        self.update_image()

    def set_target_roi(self, target: str, roi: tuple | None, *, eye_slot: str | None = None) -> None:
        """Replace ``(target, eye_slot)``'s stored ROI without emitting ``target_roi_changed``.

        Passing ``roi=None`` drops the rectangle for that slot.
        ``eye_slot`` defaults to the active eye.
        """
        slot = self._resolve_slot(eye_slot)
        if roi is None:
            self._drop_slot(self._target_rois, target, slot)
        else:
            self._target_rois.setdefault(target, {})[slot] = tuple(roi)
        self.update_image()

    def clear_target_roi(self, target: str, *, eye_slot: str | None = None) -> None:
        """Drop the ROI(s) stored for ``target`` and emit ``target_roi_changed``.

        ``eye_slot=None`` drops every slot for ``target`` (used on
        plugin swap / Clear All); pass an explicit slot to clear only
        one eye's rectangle.
        """
        if eye_slot is None:
            if self._target_rois.pop(target, None) is None:
                return
        elif not self._drop_slot(self._target_rois, target, eye_slot):
            return
        self.target_roi_changed.emit(target, None)
        self.update_image()

    def get_target_roi(self, target: str, *, eye_slot: str | None = None) -> tuple | None:
        """Return the ROI stored for ``(target, eye_slot)`` (or None)."""
        slot = self._resolve_slot(eye_slot)
        return self._target_rois.get(target, {}).get(slot)

    def clear_all_target_rois(self) -> None:
        """Drop every stored ROI across all targets and eyes (no signals)."""
        if not self._target_rois:
            return
        self._target_rois.clear()
        self.update_image()

    # ----- Per-eye, per-target threshold-mask overlays -----

    def set_target_mask(self, target: str, mask: "np.ndarray | None", *, eye_slot: str | None = None) -> None:
        """Store ``(target, eye_slot)``'s threshold mask. Repaints when masks are visible."""
        slot = self._resolve_slot(eye_slot)
        if mask is None:
            removed = self._drop_slot(self._target_masks, target, slot)
            if removed and self._show_target_masks.get(target):
                self.update_image()
            return
        self._target_masks.setdefault(target, {})[slot] = mask
        if self._show_target_masks.get(target):
            self.update_image()

    def set_show_target_mask(self, target: str, on: bool) -> None:
        """Toggle visibility of every stored mask for ``target`` (across all eyes)."""
        self._show_target_masks[target] = bool(on)
        self.update_image()

    def clear_target_mask(self, target: str, *, eye_slot: str | None = None) -> None:
        """Drop the mask for ``(target, eye_slot)`` (or every slot when ``eye_slot`` is None)."""
        was_visible = bool(self._show_target_masks.get(target)) and bool(self._target_masks.get(target))
        if eye_slot is None:
            if self._target_masks.pop(target, None) is not None and was_visible:
                self.update_image()
            return
        if self._drop_slot(self._target_masks, target, eye_slot) and was_visible:
            self.update_image()

    def get_target_mask(self, target: str, *, eye_slot: str | None = None) -> "np.ndarray | None":
        """Return the mask stored for ``(target, eye_slot)`` (or None)."""
        slot = self._resolve_slot(eye_slot)
        return self._target_masks.get(target, {}).get(slot)

    def clear_all_target_masks(self) -> None:
        """Drop every stored mask across all targets and eyes. Repaints if anything was visible."""
        if not self._target_masks:
            return
        had_visible = any(self._show_target_masks.get(t) for t in self._target_masks)
        self._target_masks.clear()
        if had_visible:
            self.update_image()

    def eventFilter(self, source: QWidget, event: QEvent) -> bool:  # noqa: N802
        """Filter events for window state changes."""
        if (
            source == self.scroll_area.viewport()
            and event.type() == QEvent.Wheel
            and event.modifiers() == Qt.ControlModifier
        ):
            zoom_in = event.angleDelta().y() > 0
            self.zoom(zoom_in, event.position().toPoint())
            return True  # Event handled, don't propagate further

        return super().eventFilter(source, event)  # Propagate other events

    def zoom(self, zoom_in: bool, pos: QPoint) -> None:
        """Zoom in or out at the specified position."""
        old_factor = self.factor
        if zoom_in:
            self.factor *= 1.1
        else:
            self.factor /= 1.1

        self.factor = max(0.1, min(25, self.factor))  # Limit zoom level

        # Calculate the new scroll position to keep the point under the cursor fixed
        viewport_center = self.scroll_area.viewport().rect().center()
        scene_pos = self.scroll_area.mapToGlobal(viewport_center) - self.mapToGlobal(QPoint(0, 0))
        delta = pos - scene_pos

        h_bar = self.scroll_area.horizontalScrollBar()
        v_bar = self.scroll_area.verticalScrollBar()

        h_bar.setValue(int(h_bar.value() + delta.x() * (self.factor / old_factor - 1)))
        v_bar.setValue(int(v_bar.value() + delta.y() * (self.factor / old_factor - 1)))

        self.update_image()

    def pause_updates(self) -> None:
        """Suppress ``update_image`` repaints until ``resume_updates`` matches.

        Reference-counted, so callers can nest pause/resume blocks safely. Use
        when a code path mutates many viewer-state setters in sequence to
        avoid the per-setter full-canvas repaint.
        """
        self._updates_paused += 1

    def resume_updates(self) -> None:
        """End a ``pause_updates`` block; repaint once if anything requested it."""
        if self._updates_paused == 0:
            return
        self._updates_paused -= 1
        if self._updates_paused == 0 and self._update_pending:
            self._update_pending = False
            self.update_image()

    def update_image(self) -> None:
        """Update the displayed image with annotations."""
        if self._updates_paused:
            self._update_pending = True
            return
        if self.original_pixmap is None or self.original_pixmap.isNull():
            return
        # The brightness-adjusted pixmap is identical to the original when
        # the factor is 1.0; with any other factor it carries the clipped
        # numpy-rescaled grayscale rebuilt by ``_rebuild_brightness_pixmap``.
        source_pixmap = self._brightness_pixmap or self.original_pixmap
        scaled_pixmap = source_pixmap.scaled(
            source_pixmap.size() * self.factor,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.pixmap = QPixmap(scaled_pixmap.size())
        self.pixmap.fill(Qt.transparent)
        painter = QPainter(self.pixmap)
        painter.drawPixmap(0, 0, scaled_pixmap)

        self.draw_eye_annotations(painter, "left")
        if self.binocular_mode:
            self.draw_eye_annotations(painter, "right")

        self._draw_detection_overlays(painter)
        self._draw_target_rois(painter)
        if self.binocular_mode:
            self._draw_inactive_half_dim(painter)
            self._draw_divider(painter)

        painter.end()
        self.image_label.setPixmap(self.pixmap)
        self.image_label.resize(self.pixmap.size())

    def draw_eye_annotations(self, painter: QPainter, eye: str) -> None:
        """Draw all annotations for a specific eye with eye label.

        Args:
            painter: QPainter object to draw with
            eye: "left" or "right"

        """
        eye_data = self.eye_data[eye]

        # Draw points for this eye
        self.draw_points_for_eye(painter, eye_data, eye)

        # Draw ellipses for this eye
        self.draw_ellipses_for_eye(painter, eye_data)

    def draw_points_for_eye(self, painter: QPainter, eye_data: dict, eye: str) -> None:
        """Draw annotation points for a specific eye."""
        is_active = eye == self.current_eye
        eye_label = "L" if eye == "left" else "R"

        for points, color, annotation_type in [
            (eye_data["pupil_points"], self.pupil_color, "pupil"),
            (eye_data["limbus_points"], self.limbus_color, "limbus"),
            (eye_data["eyelid_contour_points"], self.eyelid_color, "eyelid_contour"),
            (eye_data["glint_points"], self.glint_color, "glint"),
        ]:
            if annotation_type in self._auto_managed_annotations:
                continue
            for point in points:
                scaled_point = QPointF(point.x() * self.factor, point.y() * self.factor)
                # Only show selection highlight for active eye
                if is_active and point == self.selected_point and self.current_annotation == annotation_type:
                    if annotation_type == "pupil":
                        painter.setPen(QPen(self.pupil_select_color, 3, Qt.SolidLine))
                    elif annotation_type == "limbus":
                        painter.setPen(QPen(self.limbus_select_color, 3, Qt.SolidLine))
                    elif annotation_type == "eyelid_contour":
                        painter.setPen(QPen(self.eyelid_select_color, 3, Qt.SolidLine))
                    else:  # glint
                        painter.setPen(QPen(self.glint_select_color, 3, Qt.SolidLine))
                else:
                    painter.setPen(QPen(color, 3, Qt.SolidLine))
                painter.drawEllipse(scaled_point, 1.5, 1.5)

                # In monocular mode the L/R distinction is meaningless, so
                # the per-point eye label is suppressed entirely.
                if self.binocular_mode:
                    font = painter.font()
                    font.setPointSize(8)
                    painter.setFont(font)
                    painter.setPen(QPen(color, 1, Qt.SolidLine))
                    text_pos = QPointF(scaled_point.x() + 6, scaled_point.y() - 4)
                    painter.drawText(text_pos, eye_label)

    def draw_ellipses_for_eye(self, painter: QPainter, eye_data: dict) -> None:
        """Draw fitted ellipses + their centre markers for a specific eye."""
        if eye_data["pupil_ellipse"] and "pupil" not in self._auto_managed_annotations:
            painter.setPen(QPen(self.pupil_ellipse_color, 1, Qt.SolidLine))
            self.draw_single_ellipse(painter, eye_data["pupil_ellipse"])
            self._draw_ellipse_center(painter, eye_data["pupil_ellipse"], self.pupil_center_color)
        if eye_data["limbus_ellipse"] and "limbus" not in self._auto_managed_annotations:
            painter.setPen(QPen(self.limbus_ellipse_color, 1, Qt.SolidLine))
            self.draw_single_ellipse(painter, eye_data["limbus_ellipse"])
            self._draw_ellipse_center(painter, eye_data["limbus_ellipse"], self.limbus_ellipse_color)

    def _draw_ellipse_center(self, painter: QPainter, ellipse: tuple, color: QColor) -> None:
        """Render a small filled dot at the centre of a manually fitted ellipse."""
        if ellipse is None:
            return
        center, _size, _angle = ellipse
        scaled = QPointF(center.x() * self.factor, center.y() * self.factor)
        painter.save()
        painter.setBrush(color)
        painter.setPen(QPen(color, 1, Qt.SolidLine))
        painter.drawEllipse(scaled, 2.0, 2.0)
        painter.restore()

    def _draw_detection_overlays(self, painter: QPainter) -> None:
        """Render every per-eye, per-target Auto Detect overlay via the plugins.

        Each plugin owns its ``draw_overlay(painter, result, scale)`` and
        declares an integer ``overlay_z_order`` — overlays are drawn in
        ascending z-order so a plugin can sit visually behind or on top
        of others (e.g. limbus iris ring goes under the pupil + glint
        markers). Both eyes' stored results paint in the same pass so
        the user sees their work on every half they've processed.
        """
        # Threshold-mask fills go under the markers so the centres and
        # ellipses remain legible on top of the mask. Each plugin's mask
        # paints only when its "Show mask" toggle is on.
        self._draw_target_masks(painter)
        pairs: list[tuple[int, object, dict]] = []
        for target, by_slot in self._detection_overlays.items():
            plugin = self._active_plugins.get(target)
            if plugin is None:
                continue
            z = int(getattr(plugin, "overlay_z_order", 0))
            for result in by_slot.values():
                if result is None:
                    continue
                pairs.append((z, plugin, result))
        pairs.sort(key=itemgetter(0))
        for _z, plugin, result in pairs:
            plugin.draw_overlay(painter, result, self.factor)

    def _draw_target_masks(self, painter: QPainter) -> None:
        """Render every visible per-eye threshold mask as a semi-transparent fill.

        Colour is read from the active plugin's :attr:`mask_color`; a
        plugin that didn't declare one falls back to neutral white so
        the mask is still visible. The Show mask toggle is per-target,
        so flipping it reveals both eyes' masks at once.
        """
        for target, by_slot in self._target_masks.items():
            if not self._show_target_masks.get(target):
                continue
            plugin = self._active_plugins.get(target)
            color = getattr(plugin, "mask_color", None) or self._fallback_mask_color
            for mask in by_slot.values():
                if mask is None:
                    continue
                self._draw_mask(painter, mask, color)

    def _draw_mask(self, painter: QPainter, mask: "np.ndarray", color: QColor) -> None:
        """Blit a uint8 0/255 mask onto the canvas as a single coloured RGBA fill."""
        if mask.size == 0:
            return
        h, w = mask.shape[:2]
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        bool_mask = mask > 0
        rgba[bool_mask, 0] = color.red()
        rgba[bool_mask, 1] = color.green()
        rgba[bool_mask, 2] = color.blue()
        rgba[bool_mask, 3] = color.alpha()
        rgba = np.ascontiguousarray(rgba)
        qimg = QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888)
        scaled = QPixmap.fromImage(qimg).scaled(
            int(w * self.factor),
            int(h * self.factor),
            Qt.IgnoreAspectRatio,
            Qt.FastTransformation,
        )
        painter.drawPixmap(0, 0, scaled)

    def _draw_target_rois(self, painter: QPainter) -> None:
        """Render every stored per-eye ROI rectangle.

        Colour is read from the active plugin's :attr:`roi_color`; a
        plugin that didn't declare one falls back to neutral white.
        Only the active eye's rectangle for the active drag-edit target
        gets corner handles — the inactive eye's rectangle paints
        plain so the user sees their saved ROI without it tempting
        edit attempts (clicks on the inactive half are ignored
        anyway).
        """
        active_slot = self.active_eye_slot()
        for target, by_slot in self._target_rois.items():
            plugin = self._active_plugins.get(target)
            color = getattr(plugin, "roi_color", None) or self._fallback_roi_color
            for slot, roi in by_slot.items():
                if roi is None:
                    continue
                is_active = target == self._active_roi_target and slot == active_slot
                self._draw_target_roi_box(painter, roi, color, active=is_active)

    def _draw_target_roi_box(
        self,
        painter: QPainter,
        roi: tuple,
        color: QColor,
        *,
        active: bool,
    ) -> None:
        """Render one per-target ROI rectangle, with corner handles when ``active``."""
        x, y, w, h = roi
        scaled_x = x * self.factor
        scaled_y = y * self.factor
        scaled_w = w * self.factor
        scaled_h = h * self.factor
        painter.setPen(QPen(color, 2, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(int(scaled_x), int(scaled_y), int(scaled_w), int(scaled_h))
        if active:
            handle_size = 8
            painter.setPen(QPen(color, 2, Qt.SolidLine))
            painter.setBrush(color)
            for cx, cy in (
                (scaled_x, scaled_y),
                (scaled_x + scaled_w, scaled_y),
                (scaled_x, scaled_y + scaled_h),
                (scaled_x + scaled_w, scaled_y + scaled_h),
            ):
                painter.drawRect(
                    int(cx - handle_size / 2),
                    int(cy - handle_size / 2),
                    handle_size,
                    handle_size,
                )

    def _draw_divider(self, painter: QPainter) -> None:
        """Draw the vertical binocular divider as a dashed line."""
        if self.pixmap is None or self.pixmap.isNull():
            return
        x = self._divider_x_image() * self.factor
        height = self.pixmap.height()
        painter.save()
        painter.setPen(QPen(self.divider_color, 2, Qt.DashLine))
        painter.drawLine(int(x), 0, int(x), height)
        painter.restore()

    def _draw_inactive_half_dim(self, painter: QPainter) -> None:
        """Wash the half of the canvas not owned by the active eye with a low-alpha fill."""
        if self.pixmap is None or self.pixmap.isNull():
            return
        canvas_width = self.pixmap.width()
        canvas_height = self.pixmap.height()
        divider_canvas_x = int(self._divider_x_image() * self.factor)
        if self.current_eye == "left":
            rect = (divider_canvas_x, 0, canvas_width - divider_canvas_x, canvas_height)
        else:
            rect = (0, 0, divider_canvas_x, canvas_height)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.inactive_eye_dim_color)
        painter.drawRect(*rect)
        painter.restore()

    def fit_annotation(self) -> bool:
        """Fit an ellipse to the current annotation points."""
        if self.current_annotation in {"pupil", "limbus"}:
            return self.fit_ellipse()
        return False

    def draw_points(self, painter: QPainter) -> None:
        """Draw annotation points on the image."""
        for points, color, annotation_type in [
            (self.pupil_points, self.pupil_color, "pupil"),
            (self.limbus_points, self.limbus_color, "limbus"),
            (self.eyelid_contour_points, self.eyelid_color, "eyelid_contour"),
            (self.glint_points, self.glint_color, "glint"),
        ]:
            for point in points:
                scaled_point = QPointF(point.x() * self.factor, point.y() * self.factor)
                if point == self.selected_point and self.current_annotation == annotation_type:
                    if annotation_type == "pupil":
                        painter.setPen(QPen(self.pupil_select_color, 3, Qt.SolidLine))
                    elif annotation_type == "limbus":
                        painter.setPen(QPen(self.limbus_select_color, 3, Qt.SolidLine))
                    elif annotation_type == "eyelid_contour":
                        painter.setPen(QPen(self.eyelid_select_color, 3, Qt.SolidLine))
                    else:  # glint
                        painter.setPen(QPen(self.glint_select_color, 3, Qt.SolidLine))
                else:
                    painter.setPen(QPen(color, 3, Qt.SolidLine))
                painter.drawEllipse(scaled_point, 1.5, 1.5)

    def draw_ellipses(self, painter: QPainter) -> None:
        """Draw fitted ellipses on the image."""
        if self.pupil_ellipse:
            painter.setPen(QPen(self.pupil_ellipse_color, 1, Qt.SolidLine))
            self.draw_single_ellipse(painter, self.pupil_ellipse)
        if self.limbus_ellipse:
            painter.setPen(QPen(self.limbus_ellipse_color, 1, Qt.SolidLine))
            self.draw_single_ellipse(painter, self.limbus_ellipse)

    def draw_single_ellipse(self, painter: QPainter, ellipse: tuple | None) -> None:
        """Draw a single ellipse on the image."""
        if ellipse is None:
            return
        center, size, angle = ellipse
        scaled_center = QPointF(center.x() * self.factor, center.y() * self.factor)
        scaled_size = QSizeF(size.width() * self.factor, size.height() * self.factor)
        painter.save()
        painter.translate(scaled_center)
        painter.rotate(angle)
        painter.drawEllipse(QPointF(0, 0), scaled_size.width() / 2, scaled_size.height() / 2)
        painter.restore()

    def find_closest_point_and_type(self, pos: QPointF) -> tuple[QPointF | None, str | None]:
        """Find the closest point and its annotation type.

        Skips any annotation type currently owned by an auto detector so
        the user can't accidentally drag a hidden manual point belonging
        to an auto-managed target.
        """
        pupil_point = find_closest_point(self.pupil_points, pos, self.factor)
        limbus_point = find_closest_point(self.limbus_points, pos, self.factor)
        eyelid_point = find_closest_point(self.eyelid_contour_points, pos, self.factor)
        glint_point = find_closest_point(self.glint_points, pos, self.factor)

        closest_point = None
        closest_type = None
        min_dist = float("inf")

        for point, point_type in [
            (pupil_point, "pupil"),
            (limbus_point, "limbus"),
            (eyelid_point, "eyelid_contour"),
            (glint_point, "glint"),
        ]:
            if point and point_type not in self._auto_managed_annotations:
                dist = (point.x() - pos.x()) ** 2 + (point.y() - pos.y()) ** 2
                if dist < min_dist:
                    min_dist = dist
                    closest_point = point
                    closest_type = point_type

        return closest_point, closest_type

    def get_image_position(self, pos: QPoint) -> QPointF | None:
        """Convert widget position to image coordinates."""
        if self.pixmap:
            widget_pos = self.scroll_area.mapFrom(self, pos)
            image_pos = self.image_label.mapFrom(self.scroll_area, widget_pos)
            scaled_pos = QPointF(image_pos.x() / self.factor, image_pos.y() / self.factor)
            if (
                0 <= scaled_pos.x() < self.original_pixmap.width()
                and 0 <= scaled_pos.y() < self.original_pixmap.height()
            ):
                return scaled_pos
        return None

    def set_current_annotation(self, annotation_type: str) -> None:
        """Set the current annotation type."""
        if self.current_annotation != annotation_type:
            self.current_annotation = annotation_type
            self.annotation_type_changed.emit(self.current_annotation)  # Emit the new signal
        self.annotation_changed.emit()

    def clear_pupil_points(self) -> None:
        """Clear all pupil annotation points."""
        self.pupil_points = []
        self.pupil_ellipse = None
        self.save_state()
        self.save_current_eye_data()
        self.annotation_changed.emit()
        self.update_image()

    def clear_limbus_points(self) -> None:
        """Clear all limbus annotation points."""
        self.limbus_points = []
        self.limbus_ellipse = None
        self.save_state()
        self.save_current_eye_data()
        self.annotation_changed.emit()
        self.update_image()

    def clear_limbus_ellipse(self) -> None:
        """Clear the fitted limbus ellipse."""
        self.limbus_ellipse = None
        self.save_state()
        self.save_current_eye_data()
        self.annotation_changed.emit()
        self.update_image()

    def clear_pupil_ellipse(self) -> None:
        """Clear the fitted pupil ellipse."""
        self.pupil_ellipse = None
        self.save_state()
        self.save_current_eye_data()
        self.annotation_changed.emit()
        self.update_image()

    def clear_eyelid_points(self) -> None:
        """Clear all eyelid contour points."""
        self.eyelid_contour_points = []
        self.save_state()
        self.save_current_eye_data()
        self.annotation_changed.emit()
        self.update_image()

    def clear_glint_points(self) -> None:
        """Clear all glint points."""
        self.glint_points = []
        self.save_state()
        self.save_current_eye_data()
        self.annotation_changed.emit()
        self.update_image()

    def clear_all(self) -> None:
        """Clear all annotations."""
        self.clear_pupil_points()
        self.clear_limbus_points()
        self.clear_eyelid_points()
        self.clear_glint_points()

    def get_annotation_data(self) -> dict:
        """Get all annotation data for both eyes."""
        return self.get_all_eye_data()

    def set_annotation_data(self, data: dict) -> None:
        """Set annotation data for both eyes."""
        self.set_all_eye_data(data)
        self.reset_undo_stack(initial_state=self.get_current_state())

    def fit_ellipse(self) -> bool:
        """Fit an ellipse to annotation points."""
        points = self.pupil_points if self.current_annotation == "pupil" else self.limbus_points
        if len(points) >= 5:
            x = np.array([p.x() for p in points])
            y = np.array([p.y() for p in points])
            params = fit_ellipse(x, y)
            center = QPointF(params[0], params[1])
            size = QSizeF(2 * params[2], 2 * params[3])
            angle = np.degrees(params[4])
            if self.current_annotation == "pupil":
                self.pupil_ellipse = (center, size, angle)
            else:
                self.limbus_ellipse = (center, size, angle)
            self.save_state()
            self.save_current_eye_data()
            self.annotation_changed.emit()
            self.update_image()
            return True
        if len(points) != 0:
            QMessageBox.warning(
                self,
                "Warning",
                f"At least 5 points are required to fit the {self.current_annotation} ellipse.",
            )
        return False

    def clear_selected_ellipse(self) -> None:
        """Clear the currently selected ellipse."""
        if self.current_annotation == "pupil":
            self.clear_pupil_ellipse()
        elif self.current_annotation == "limbus":
            self.clear_limbus_ellipse()

    @staticmethod
    def move_points_by_delta(points: list[QPointF], delta_x: float, delta_y: float) -> None:
        """Helper method to move all points in a list by a given delta."""
        for i in range(len(points)):
            points[i] = QPointF(points[i].x() + delta_x, points[i].y() + delta_y)

    @staticmethod
    def is_point_in_roi(point: QPointF, roi: tuple | None) -> bool:
        """Check if a point is inside the given rectangle."""
        if not roi:
            return False
        x, y, w, h = roi
        return x <= point.x() <= x + w and y <= point.y() <= y + h

    def get_roi_handle_at_pos(self, point: QPointF, roi: tuple | None) -> str | None:
        """Get the corner-handle name (tl/tr/bl/br) at ``point`` for the given rectangle."""
        if not roi:
            return None

        x, y, w, h = roi
        handle_size = 8 / self.factor  # in image coordinates

        if abs(point.x() - x) < handle_size and abs(point.y() - y) < handle_size:
            return "tl"
        if abs(point.x() - (x + w)) < handle_size and abs(point.y() - y) < handle_size:
            return "tr"
        if abs(point.x() - x) < handle_size and abs(point.y() - (y + h)) < handle_size:
            return "bl"
        if abs(point.x() - (x + w)) < handle_size and abs(point.y() - (y + h)) < handle_size:
            return "br"

        return None
