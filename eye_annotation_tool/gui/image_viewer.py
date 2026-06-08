"""Image viewer widget for displaying and annotating eye images."""

from typing import ClassVar

import cv2
import numpy as np
from PyQt5.QtCore import QEvent, QPoint, QPointF, QSizeF, Qt, pyqtSignal
from PyQt5.QtGui import QKeyEvent, QPixmap, QResizeEvent
from PyQt5.QtWidgets import QLabel, QMessageBox, QScrollArea, QVBoxLayout, QWidget

from ..state import EyeDataStore, OverlayStore, TargetRoiStore, UndoStack
from ..state.eye_data_store import FIELDS_BY_ANNOTATION
from ..utils.image_processing import find_closest_point, fit_ellipse
from .brightness_controller import BrightnessController
from .canvas_renderer import AnnotationColors, CanvasGeometry, CanvasRenderer, OverlayStateLookup
from .mouse_drag_state import MouseDragState
from .zoom_controller import ZoomController


class _ElidedPathLabel(QLabel):
    """A single-line label that middle-elides its text to fit the available width.

    The full text is kept as the tooltip so the complete path is always
    reachable on hover even when the displayed text is truncated.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty elided label."""
        super().__init__(parent)
        self._full_text = ""

    def set_full_text(self, text: str) -> None:
        """Store ``text`` as the full path and render an elided version of it."""
        self._full_text = text
        self.setToolTip(text)
        self._apply_elide()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Re-elide on width changes so the visible text always fits."""
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideMiddle, self.width()))


class ImageViewer(QWidget):
    """Widget for viewing and annotating eye images with pupil, limbus, eyelid, and glint markers."""

    # Detector kind slug (in project settings + the orchestrator) to
    # the manual annotation slug (used by ``current_annotation`` and the
    # eye_data dict fields).
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
    # per-kind Auto Detect ROI on the canvas. Payload is the kind slug
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
        self.colors = AnnotationColors.default()
        self._overlay_state_lookup = lambda _target: None
        self.renderer = CanvasRenderer(
            self.colors,
            self.eye_data_store,
            self.detection_overlays,
            self.target_rois,
            self._overlay_state_lookup,
            self.brightness,
            self.zoom_state,
        )

        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_MouseTracking, True)
        self.setMouseTracking(True)

    def setup_ui(self) -> None:
        """Set up the user interface components."""
        layout = QVBoxLayout()
        self.path_label = _ElidedPathLabel()
        self.path_label.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(self.path_label)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.image_label)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.scroll_area)
        self.setLayout(layout)

        self.scroll_area.viewport().installEventFilter(self)

    def set_image_path_text(self, text: str) -> None:
        """Show ``text`` (an image path) in the breadcrumb strip above the image."""
        self.path_label.set_full_text(text)

    def setup_variables(self) -> None:
        """Initialize instance variables."""
        self.zoom_state = ZoomController()
        self.eye_data_store = EyeDataStore()

        self.current_annotation = "pupil"
        self.original_pixmap = None
        self.pixmap = None

        # Mutable drag-state flags + coordinates spanning the three
        # mouse-event handlers. Grouped into one dataclass so the
        # widget exposes a single ``self.mouse_state`` instead of
        # thirteen mutable attributes. ``drawing_roi_committed`` flips
        # True only once the user has dragged past :attr:`ROI_DRAG_THRESHOLD_PX`
        # since press — below that the previous rectangle is preserved
        # and the release path skips re-emitting, so a stray click
        # doesn't clear a valid ROI.
        self.mouse_state = MouseDragState()

        # Display-only brightness adjustment. The source pixmap is
        # never modified, so detector plugins keep seeing the original
        # image; the controller caches an adjusted pixmap built from
        # the numpy grayscale view.
        self.brightness = BrightnessController()

        # Batch-update gate. Setters call ``update_image()`` to repaint after
        # mutating state; on heavy load paths (apply_loaded_detections,
        # live-plugin refresh for both eyes) those calls stack up into many
        # full-canvas paints. ``_updates_paused`` collapses them: while True,
        # ``update_image`` marks ``_update_pending`` and returns; the
        # matching ``resume_updates`` issues a single repaint if any was
        # requested. Depth-counted so nested pause/resume composes safely.
        self._updates_paused = 0
        self._update_pending = False

        # Binocular mode: True when the image contains two eyes split by
        # a vertical divider. False = monocular (single eye fills the
        # image, ``current_eye`` is pinned to "left", divider + right
        # block are never drawn).
        self.binocular_mode = True

        # Vertical divider position as a fraction of image width in
        # ``[0, 1]``. Only used when ``binocular_mode`` is True; defines
        # which half of the image counts as the left eye vs the right
        # eye for click gating, dim-overlay rendering, and auto-detector
        # cropping. The in-progress drag flag lives on ``mouse_state``.
        self._divider_x_norm = 0.5

        # Grayscale numpy view of the currently loaded image, kept alongside
        # the Qt pixmap so detector plugins can consume it without re-reading
        # from disk.
        self.image_grayscale: np.ndarray | None = None

        # Per-eye, per-kind Auto Detect overlay results. Outer key is
        # the anatomical kind ("pupil" / "glint" / "limbus" / "eyelid"),
        # inner key the eye slot ("left" / "right" / "single"). Both
        # eyes' overlays paint at the same time so the user can see at
        # a glance which halves they've already processed; the dim wash
        # over the inactive half indicates which side they're currently
        # working on. The inner dict's value shape matches the
        # corresponding plugin's serialise/deserialise contract.
        self.detection_overlays = OverlayStore()

        # Detector kinds currently owned by an auto detector. Manual
        # painting and click-to-place for these kinds is suppressed so
        # each kind has a single source of truth (auto OR manual).
        self._auto_managed_targets: set[str] = set()
        self._auto_managed_annotations: set[str] = set()

        # Per-eye, per-kind Auto Detect ROI rectangles plus the
        # active drag-edit kind. Drag handles + canvas clicks only
        # operate on the active eye's rectangle for the active kind;
        # the inactive eye's rectangle paints without handles for context.
        self.target_rois = TargetRoiStore()

    @property
    def factor(self) -> float:
        """Current zoom factor (read-only mirror of :attr:`ZoomController.factor`)."""
        return self.zoom_state.factor

    def setup_undo_system(self) -> None:
        """Initialise the undo stack."""
        self.undo_stack = UndoStack[dict](maxlen=10)

    # ---------------------------------------------------------------------------
    # Active-eye annotation properties (forward to :class:`EyeDataStore`)
    # ---------------------------------------------------------------------------

    @property
    def current_eye(self) -> str:
        """Active eye for canvas edits (``"left"`` or ``"right"``)."""
        return self.eye_data_store.current_eye

    @property
    def eye_data(self) -> dict:
        """Live ``{eye: {field: value}}`` dict (mutate in place)."""
        return self.eye_data_store.eye_data

    @property
    def pupil_points(self) -> list:
        """Live pupil-point list for the active eye."""
        return self.eye_data_store.get_field("pupil_points")

    @pupil_points.setter
    def pupil_points(self, value: list) -> None:
        self.eye_data_store.set_field("pupil_points", value)

    @property
    def limbus_points(self) -> list:
        """Live limbus-point list for the active eye."""
        return self.eye_data_store.get_field("limbus_points")

    @limbus_points.setter
    def limbus_points(self, value: list) -> None:
        self.eye_data_store.set_field("limbus_points", value)

    @property
    def eyelid_contour_points(self) -> list:
        """Live eyelid-contour-point list for the active eye."""
        return self.eye_data_store.get_field("eyelid_contour_points")

    @eyelid_contour_points.setter
    def eyelid_contour_points(self, value: list) -> None:
        self.eye_data_store.set_field("eyelid_contour_points", value)

    @property
    def glint_points(self) -> list:
        """Live glint-point list for the active eye."""
        return self.eye_data_store.get_field("glint_points")

    @glint_points.setter
    def glint_points(self, value: list) -> None:
        self.eye_data_store.set_field("glint_points", value)

    @property
    def pupil_ellipse(self) -> tuple | None:
        """Fitted pupil ellipse for the active eye (``None`` when unset)."""
        return self.eye_data_store.get_field("pupil_ellipse")

    @pupil_ellipse.setter
    def pupil_ellipse(self, value: tuple | None) -> None:
        self.eye_data_store.set_field("pupil_ellipse", value)

    @property
    def limbus_ellipse(self) -> tuple | None:
        """Fitted limbus ellipse for the active eye (``None`` when unset)."""
        return self.eye_data_store.get_field("limbus_ellipse")

    @limbus_ellipse.setter
    def limbus_ellipse(self, value: tuple | None) -> None:
        self.eye_data_store.set_field("limbus_ellipse", value)

    def switch_eye(self, eye: str) -> None:
        """Switch between left and right eye annotations."""
        if eye not in {"left", "right"}:
            return
        # In monocular mode the right block is unused; defend against
        # programmatic callers requesting "right" so saves stay in sync
        # with the left-only convention.
        if not self.binocular_mode and eye != "left":
            return
        self.eye_data_store.switch_eye(eye)
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
            self.eye_data_store.switch_eye("left")
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
        return self.eye_data_store.as_dict()

    def set_all_eye_data(self, eye_data: dict) -> None:
        """Set annotation data for both eyes."""
        self.eye_data_store.from_dict(eye_data)
        self.update_image()

    def reset_undo_stack(self, initial_state: dict | None = None) -> None:
        """Drop history and seed the stack with the current annotation state (or ``initial_state``)."""
        self.undo_stack.reset(initial_state if initial_state is not None else self._snapshot_state())

    def _snapshot_state(self) -> dict:
        """Capture the active eye's annotation fields as an undo-stack entry."""
        return {
            "pupil_points": self.pupil_points.copy(),
            "limbus_points": self.limbus_points.copy(),
            "eyelid_contour_points": self.eyelid_contour_points.copy(),
            "glint_points": self.glint_points.copy(),
            "pupil_ellipse": self.pupil_ellipse,
            "limbus_ellipse": self.limbus_ellipse,
        }

    def save_state(self) -> None:
        """Push the current annotation state onto the undo stack."""
        self.undo_stack.push(self._snapshot_state())

    def can_undo(self) -> bool:
        """True when there's a previous state to step back to."""
        return self.undo_stack.can_undo()

    def undo(self) -> None:
        """Restore the previous annotation state (no-op when at the start of history)."""
        state = self.undo_stack.undo()
        if state is None:
            return
        self.pupil_points = state["pupil_points"].copy()
        self.limbus_points = state["limbus_points"].copy()
        self.eyelid_contour_points = state.get("eyelid_contour_points", []).copy()
        self.glint_points = state.get("glint_points", []).copy()
        self.pupil_ellipse = state["pupil_ellipse"]
        self.limbus_ellipse = state["limbus_ellipse"]
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
            self.mouse_state.shift_pressed = True
        elif event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self.mouse_state.space_pressed = True
            if not self.mouse_state.panning:
                self.setCursor(Qt.OpenHandCursor)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Handle key release events."""
        if event.key() == Qt.Key_Shift:
            self.mouse_state.shift_pressed = False
        elif event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self.mouse_state.space_pressed = False
            # If a space-pan is mid-drag, keep the closed-hand cursor until the
            # mouse is released; otherwise drop straight back to the arrow.
            if not self.mouse_state.panning:
                self.setCursor(Qt.ArrowCursor)
        super().keyReleaseEvent(event)

    def delete_selected_point(self) -> None:
        """Delete the currently selected point from the active annotation's list."""
        if self.mouse_state.selected_point is None:
            return
        points_field, _ = FIELDS_BY_ANNOTATION[self.current_annotation]
        points = self.eye_data_store.get_field(points_field)
        if self.mouse_state.selected_point not in points:
            return
        points.remove(self.mouse_state.selected_point)
        self.mouse_state.selected_point = None
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def _active_drag_roi(self) -> tuple | None:
        """Return the rectangle currently being drag-edited on the active eye (or None)."""
        return self.target_rois.active_drag_roi(self.active_eye_slot())

    def _set_active_drag_roi(self, value: tuple | None) -> None:
        """Write the in-progress drag rectangle to the active eye's slot for the active kind."""
        if self.target_rois.active_target is None:
            return
        self.target_rois.set(self.target_rois.active_target, self.active_eye_slot(), value)

    # Minimum cursor movement (image coords) before a press counts as a
    # drag-to-draw rather than a click. Below this, the rectangle stays
    # uncommitted so an accidental click on an active ROI kind does
    # not produce an invisible / one-pixel rectangle.
    ROI_DRAG_THRESHOLD_PX = 5.0

    def _drag_active_roi(self, new_pos: QPointF) -> None:
        """Update the active drag ROI in place based on the current cursor position.

        Dispatches by ``drawing_roi`` / ``moving_roi`` / ``resizing_roi``
        flags. The active rectangle is read via :meth:`_active_drag_roi`
        and written back via :meth:`_set_active_drag_roi`.
        """
        if self.mouse_state.drawing_roi:
            dx = new_pos.x() - self.mouse_state.roi_start_pos.x()
            dy = new_pos.y() - self.mouse_state.roi_start_pos.y()
            if max(abs(dx), abs(dy)) < self.ROI_DRAG_THRESHOLD_PX:
                return
            x = min(self.mouse_state.roi_start_pos.x(), new_pos.x())
            y = min(self.mouse_state.roi_start_pos.y(), new_pos.y())
            w = abs(dx)
            h = abs(dy)
            self._set_active_drag_roi((x, y, w, h))
            self.mouse_state.drawing_roi_committed = True
            return
        current = self._active_drag_roi()
        if current is None:
            return
        if self.mouse_state.moving_roi:
            delta_x = new_pos.x() - self.mouse_state.roi_start_pos.x()
            delta_y = new_pos.y() - self.mouse_state.roi_start_pos.y()
            x, y, w, h = current
            self._set_active_drag_roi((x + delta_x, y + delta_y, w, h))
            self.mouse_state.roi_start_pos = new_pos
            return
        if self.mouse_state.resizing_roi:
            x, y, w, h = current
            if "t" in self.mouse_state.roi_resize_handle:
                delta_y = new_pos.y() - y
                y = new_pos.y()
                h -= delta_y
            if "b" in self.mouse_state.roi_resize_handle:
                h = new_pos.y() - y
            if "l" in self.mouse_state.roi_resize_handle:
                delta_x = new_pos.x() - x
                x = new_pos.x()
                w -= delta_x
            if "r" in self.mouse_state.roi_resize_handle:
                w = new_pos.x() - x
            self._set_active_drag_roi((x, y, max(10, w), max(10, h)))

    def _try_begin_roi_drag(self, image_pos: QPointF, current: tuple | None) -> bool:
        """Convert a click on or near ``current`` into a resize/move/draw state.

        Returns True — the click is always consumed by ROI-drag mode. A
        click on the inactive eye's half in binocular mode is consumed
        without starting a fresh draw, so the user can't accidentally
        annotate the wrong side; switch eyes first.
        """
        if current:
            handle = self.get_roi_handle_at_pos(image_pos, current)
            if handle:
                self.mouse_state.resizing_roi = True
                self.mouse_state.roi_resize_handle = handle
                self.mouse_state.roi_start_pos = image_pos
                return True
            if self.is_point_in_roi(image_pos, current):
                self.mouse_state.moving_roi = True
                self.mouse_state.roi_start_pos = image_pos
                return True
        if not self._point_on_active_side(image_pos):
            return True
        # Begin a fresh draw, but keep the previous rectangle in place
        # until the user actually drags past the threshold — a stray
        # click without movement leaves the existing ROI untouched.
        self.mouse_state.drawing_roi = True
        self.mouse_state.drawing_roi_committed = False
        self.mouse_state.roi_start_pos = image_pos
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
            self.mouse_state.panning = True
            self.mouse_state.last_pan_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.LeftButton:
            # Spacebar held turns left-drag into a pan (hand tool), taking
            # priority over every annotation action below.
            if self.mouse_state.space_pressed:
                self.mouse_state.panning = True
                self.mouse_state.last_pan_pos = event.pos()
                self.setCursor(Qt.ClosedHandCursor)
                return
            image_pos = self.get_image_position(event.pos())
            if image_pos:
                # The binocular divider grabs the click before anything
                # else so the user can always retarget the line even when
                # it overlaps an ROI handle or an annotation point.
                if self._hit_divider(image_pos):
                    self.mouse_state.divider_drag_active = True
                    self.setCursor(Qt.SizeHorCursor)
                    return

                # A per-kind ROI in drag-edit mode consumes the click
                # before any Manual-mode click-to-place flow runs. Drag
                # operates on the active eye's slot for that kind —
                # the inactive eye's rectangle paints but isn't grabbable.
                if self.target_rois.active_target is not None:
                    self._try_begin_roi_drag(image_pos, self._active_drag_roi())
                    return

                self.mouse_state.selected_point, selected_annotation = self.find_closest_point_and_type(image_pos)

                if self.mouse_state.selected_point:
                    self.mouse_state.moving_point = True
                    self.mouse_state.last_mouse_pos = image_pos
                    self.mouse_state.moving_all_points = self.mouse_state.shift_pressed

                    if selected_annotation != self.current_annotation:
                        self.current_annotation = selected_annotation
                        self.annotation_type_changed.emit(self.current_annotation)
                elif self.current_annotation in self._auto_managed_annotations:
                    # The current kind is owned by an auto detector; manual
                    # click-to-place is disabled so the auto overlay stays the
                    # single source of truth for this kind.
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
                self.annotation_changed.emit()
                self.update_image()

    def mouseMoveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle mouse move events."""
        if self.mouse_state.panning:
            delta = event.pos() - self.mouse_state.last_pan_pos
            self.scroll_area.horizontalScrollBar().setValue(self.scroll_area.horizontalScrollBar().value() - delta.x())
            self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().value() - delta.y())
            self.mouse_state.last_pan_pos = event.pos()
        elif self.mouse_state.divider_drag_active:
            new_pos = self.get_image_position(event.pos())
            if new_pos is not None and self.original_pixmap is not None:
                self.set_divider_x_norm(new_pos.x() / self.original_pixmap.width())
        elif self.mouse_state.is_dragging_roi():
            new_pos = self.get_image_position(event.pos())
            if new_pos and self.mouse_state.roi_start_pos:
                self._drag_active_roi(new_pos)
                self.update_image()
        elif self.mouse_state.moving_point and self.mouse_state.selected_point:
            new_pos = self.get_image_position(event.pos())
            if new_pos and self.mouse_state.last_mouse_pos:
                delta_x = new_pos.x() - self.mouse_state.last_mouse_pos.x()
                delta_y = new_pos.y() - self.mouse_state.last_mouse_pos.y()
                points_field, _ = FIELDS_BY_ANNOTATION[self.current_annotation]
                points = self.eye_data_store.get_field(points_field)
                if self.mouse_state.moving_all_points:
                    self.move_points_by_delta(points, delta_x, delta_y)
                else:
                    index = points.index(self.mouse_state.selected_point)
                    points[index] = new_pos
                self.mouse_state.selected_point = new_pos
                self.mouse_state.last_mouse_pos = new_pos
                self.update_image()

    def mouseReleaseEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle mouse release events."""
        if event.button() == Qt.MiddleButton:
            self.mouse_state.panning = False
            self.setCursor(Qt.ArrowCursor)
        elif event.button() == Qt.LeftButton:
            if self.mouse_state.panning:
                self.mouse_state.panning = False
                self.setCursor(Qt.OpenHandCursor if self.mouse_state.space_pressed else Qt.ArrowCursor)
                return
            if self.mouse_state.divider_drag_active:
                self.mouse_state.divider_drag_active = False
                self.setCursor(Qt.ArrowCursor)
                self.divider_x_norm_changed.emit(self._divider_x_norm)
                return
            if self.mouse_state.is_dragging_roi():
                # A draw that never crossed the threshold is treated as
                # a stray click — leave the previous ROI alone and skip
                # the emit so the panel doesn't get re-notified with
                # stale data.
                was_drawing = self.mouse_state.drawing_roi
                committed = self.mouse_state.drawing_roi_committed
                self.mouse_state.reset_roi_drag()
                draw_changed_roi = (not was_drawing) or committed
                if draw_changed_roi and self.target_rois.active_target is not None:
                    kind = self.target_rois.active_target
                    self.target_roi_changed.emit(kind, self.target_rois.get(kind, self.active_eye_slot()))
                return

            self.mouse_state.moving_point = False
            if self.mouse_state.selected_point:
                self.save_state()
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
        self.brightness.rebuild(self.original_pixmap, self.image_grayscale)
        self.pupil_points = []
        self.limbus_points = []
        self.pupil_ellipse = None
        self.limbus_ellipse = None
        # Per-image Auto Detect overlay state — cleared here so the next
        # image starts blank before the annotation controller restores
        # whatever the new image's saved annotation carries. Masks are
        # transient (never persisted), so they always start empty on a
        # new image until the next plugin run.
        self.detection_overlays.clear_all()
        self.target_rois.clear_all()
        self.reset_undo_stack()
        if not self.zoom_state.is_initialized():
            self.zoom_state.fit_to_viewport(self.scroll_area, self.original_pixmap)
            self.zoom_state.mark_initialized()
        self.update_image()
        self.image_loaded.emit()
        return True

    def clear(self) -> None:
        """Reset the viewer to an empty state (no image, no overlays)."""
        self.original_pixmap = None
        self.image_grayscale = None
        self.detection_overlays.clear_all()
        self.target_rois.clear_all()
        self.pupil_points = []
        self.limbus_points = []
        self.pupil_ellipse = None
        self.limbus_ellipse = None
        self.image_label.clear()

    def zoom_in_centered(self) -> None:
        """Zoom in around the centre of the visible viewport."""
        self.zoom_state.zoom_in_centered(self.scroll_area, self)
        self.update_image()

    def zoom_out_centered(self) -> None:
        """Zoom out around the centre of the visible viewport."""
        self.zoom_state.zoom_out_centered(self.scroll_area, self)
        self.update_image()

    def reset_zoom_to_fit(self) -> None:
        """Restore the zoom factor to fit the whole image inside the viewport."""
        self.zoom_state.fit_to_viewport(self.scroll_area, self.original_pixmap)
        self.update_image()

    def set_zoom_factor(self, factor: float) -> None:
        """Set the zoom factor directly (clamped to the controller's range)."""
        clamped = max(self.zoom_state.MIN_FACTOR, min(self.zoom_state.MAX_FACTOR, float(factor)))
        if clamped == self.zoom_state.factor:
            return
        self.zoom_state.at_fit = False
        self.zoom_state.factor = clamped
        self.update_image()

    def brighten_display(self) -> None:
        """Multiply the displayed grayscale by the brightness step."""
        if self.brightness.brighten():
            self.brightness.rebuild(self.original_pixmap, self.image_grayscale)
            self.update_image()

    def darken_display(self) -> None:
        """Divide the displayed grayscale by the brightness step."""
        if self.brightness.darken():
            self.brightness.rebuild(self.original_pixmap, self.image_grayscale)
            self.update_image()

    def reset_display_brightness(self) -> None:
        """Restore the displayed grayscale to its source values."""
        if self.brightness.reset():
            self.brightness.rebuild(self.original_pixmap, self.image_grayscale)
            self.update_image()

    def set_brightness_factor(self, factor: float) -> None:
        """Set the brightness factor directly (clamped to the controller's range)."""
        if self.brightness.set_factor(float(factor)):
            self.brightness.rebuild(self.original_pixmap, self.image_grayscale)
            self.update_image()

    def get_current_image_grayscale(self) -> np.ndarray | None:
        """Return the grayscale numpy view of the current image (or None)."""
        return self.image_grayscale

    # ----- Auto Detect overlays -----

    def set_overlay_state_lookup(self, lookup: OverlayStateLookup) -> None:
        """Wire the callable that maps a kind slug to its DetectorCard overlay state."""
        self._overlay_state_lookup = lookup
        self.renderer.overlay_state_lookup = lookup
        self.update_image()

    def set_auto_managed_targets(self, kinds: set[str]) -> None:
        """Declare which detector kinds are now owned by an auto detector.

        Manual annotations and click-to-place for these kinds are
        suppressed; the auto-detector overlay is the single source for
        those kinds. Targets not in the set fall back to the manual
        annotation path.
        """
        self._auto_managed_targets = set(kinds)
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
        if not self.eye_data_store.clear_target_across_eyes(points_field, ellipse_field):
            return
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def active_eye_slot(self) -> str:
        """Return the per-eye storage key for the currently active eye."""
        return self.current_eye if self.binocular_mode else "single"

    def _resolve_slot(self, eye_slot: str | None) -> str:
        """Map ``None`` to the currently active eye slot."""
        return eye_slot if eye_slot is not None else self.active_eye_slot()

    # ----- Per-eye, per-kind Auto Detect overlays -----

    def set_detection_overlay(self, kind: str, result: dict, *, eye_slot: str | None = None) -> None:
        """Store an Auto Detect result for ``kind`` at ``eye_slot`` and re-paint.

        ``eye_slot`` defaults to the active eye; pass ``"left"`` /
        ``"right"`` / ``"single"`` explicitly when populating from a
        loaded annotation file that carries results for both eyes.
        """
        self.detection_overlays.set(kind, self._resolve_slot(eye_slot), result)
        self.update_image()

    def clear_detection_overlay(self, kind: str, *, eye_slot: str | None = None) -> None:
        """Drop the stored result for ``kind`` at ``eye_slot`` (or every slot when ``eye_slot`` is None)."""
        slot = None if eye_slot is None else self._resolve_slot(eye_slot)
        if self.detection_overlays.clear(kind, slot):
            self.update_image()

    def clear_all_detection_overlays(self) -> None:
        """Drop every stored Auto Detect result and re-paint. Called on image change."""
        if self.detection_overlays.clear_all():
            self.update_image()

    def get_detection_overlay(self, kind: str, *, eye_slot: str | None = None) -> dict | None:
        """Return the result stored for ``(kind, eye_slot)``, or None."""
        return self.detection_overlays.get(kind, self._resolve_slot(eye_slot))

    # ----- Per-eye, per-kind Auto Detect ROIs -----

    def set_active_roi_target(self, kind: str | None) -> None:
        """Enter drag-edit mode for ``kind``'s ROI, or leave it (``None``).

        Cancels any in-progress drag and re-paints so the corner-handle
        decoration follows the newly active kind.
        """
        if kind is not None and kind == self.target_rois.active_target:
            return
        self.target_rois.set_active_target(kind)
        # Cancel any drag in progress; the user toggled the active ROI
        # while pressing the mouse — rare but worth a clean reset.
        self.mouse_state.reset_roi_drag()
        self.update_image()

    def set_target_roi(self, kind: str, roi: tuple | None, *, eye_slot: str | None = None) -> None:
        """Replace ``(kind, eye_slot)``'s stored ROI without emitting ``target_roi_changed``.

        Passing ``roi=None`` drops the rectangle for that slot.
        ``eye_slot`` defaults to the active eye.
        """
        self.target_rois.set(kind, self._resolve_slot(eye_slot), roi)
        self.update_image()

    def clear_target_roi(self, kind: str, *, eye_slot: str | None = None) -> None:
        """Drop the ROI(s) stored for ``kind`` and emit ``target_roi_changed``.

        ``eye_slot=None`` drops every slot for ``kind`` (used on
        plugin swap / Clear All); pass an explicit slot to clear only
        one eye's rectangle.
        """
        slot = None if eye_slot is None else eye_slot
        if not self.target_rois.clear(kind, slot):
            return
        self.target_roi_changed.emit(kind, None)
        self.update_image()

    def get_target_roi(self, kind: str, *, eye_slot: str | None = None) -> tuple | None:
        """Return the ROI stored for ``(kind, eye_slot)`` (or None)."""
        return self.target_rois.get(kind, self._resolve_slot(eye_slot))

    def clear_all_target_rois(self) -> None:
        """Drop every stored ROI across all kinds and eyes (no signals)."""
        if self.target_rois.clear_all():
            self.update_image()

    def eventFilter(self, source: QWidget, event: QEvent) -> bool:  # noqa: N802
        """Ctrl+wheel zooms; resizing the viewport refits the image while in fit mode."""
        if source == self.scroll_area.viewport():
            if event.type() == QEvent.Wheel and event.modifiers() == Qt.ControlModifier:
                zoom_in = event.angleDelta().y() > 0
                self.zoom(zoom_in, event.position().toPoint())
                return True  # Event handled, don't propagate further
            if event.type() == QEvent.Resize and self.zoom_state.at_fit and self.original_pixmap is not None:
                self.reset_zoom_to_fit()

        return super().eventFilter(source, event)  # Propagate other events

    def zoom(self, zoom_in: bool, pos: QPoint) -> None:
        """Zoom in or out around ``pos`` (cursor position in viewer-local coords)."""
        self.zoom_state.zoom(zoom_in, pos, self.scroll_area, self)
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
        """Repaint the canvas (or buffer the request while updates are paused)."""
        if self._updates_paused:
            self._update_pending = True
            return
        geometry = CanvasGeometry(
            current_eye=self.current_eye,
            binocular_mode=self.binocular_mode,
            divider_x_image=self._divider_x_image(),
            current_annotation=self.current_annotation,
            auto_managed_annotations=self._auto_managed_annotations,
            selected_point=self.mouse_state.selected_point,
        )
        pixmap = self.renderer.render(self.original_pixmap, geometry)
        if pixmap is None:
            return
        self.pixmap = pixmap
        self.image_label.setPixmap(pixmap)
        self.image_label.resize(pixmap.size())

    def fit_annotation(self) -> bool:
        """Fit an ellipse to the current annotation points."""
        if self.current_annotation in {"pupil", "limbus"}:
            return self.fit_ellipse()
        return False

    def find_closest_point_and_type(self, pos: QPointF) -> tuple[QPointF | None, str | None]:
        """Find the closest point and its annotation type.

        Skips any annotation type currently owned by an auto detector so
        the user can't accidentally drag a hidden manual point belonging
        to an auto-managed kind.
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

    def _clear_annotation(self, annotation: str, *, ellipse_only: bool = False) -> None:
        """Clear ``annotation``'s ellipse, and (unless ``ellipse_only``) its point list.

        Shared implementation behind the public ``clear_*_points`` /
        ``clear_*_ellipse`` wrappers — the signal/slot wiring on the
        annotation panel still binds to those names so the wrappers
        stay.
        """
        points_field, ellipse_field = FIELDS_BY_ANNOTATION[annotation]
        if not ellipse_only:
            self.eye_data_store.set_field(points_field, [])
        if ellipse_field is not None:
            self.eye_data_store.set_field(ellipse_field, None)
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def clear_pupil_points(self) -> None:
        """Clear all pupil annotation points (and the fitted ellipse)."""
        self._clear_annotation("pupil")

    def clear_limbus_points(self) -> None:
        """Clear all limbus annotation points (and the fitted ellipse)."""
        self._clear_annotation("limbus")

    def clear_limbus_ellipse(self) -> None:
        """Clear the fitted limbus ellipse (point list stays)."""
        self._clear_annotation("limbus", ellipse_only=True)

    def clear_pupil_ellipse(self) -> None:
        """Clear the fitted pupil ellipse (point list stays)."""
        self._clear_annotation("pupil", ellipse_only=True)

    def clear_eyelid_points(self) -> None:
        """Clear all eyelid contour points."""
        self._clear_annotation("eyelid_contour")

    def clear_glint_points(self) -> None:
        """Clear all glint points."""
        self._clear_annotation("glint")

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
        self.reset_undo_stack()

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
