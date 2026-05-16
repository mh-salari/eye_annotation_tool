"""Image viewer widget for displaying and annotating eye images."""

from collections import deque

import cv2
import numpy as np
from PyQt5.QtCore import QEvent, QPoint, QPointF, QSizeF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeyEvent, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QLabel, QMessageBox, QScrollArea, QVBoxLayout, QWidget

from ..utils.image_processing import find_closest_point, fit_ellipse


class ImageViewer(QWidget):
    """Widget for viewing and annotating eye images with pupil, limbus, eyelid, and glint markers."""

    annotation_changed = pyqtSignal()
    annotation_type_changed = pyqtSignal(str)
    # Emitted after a new image is loaded; MainWindow uses it to re-trigger
    # live detection when Manual Threshold mode is enabled.
    image_loaded = pyqtSignal()
    # Manual Threshold ROIs: emitted whenever the user finishes drawing /
    # moving / resizing a pupil or glint ROI. Payload is (x, y, w, h) or None.
    pupil_roi_changed = pyqtSignal(object)
    glint_roi_changed = pyqtSignal(object)

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
                "roi": None,  # (x, y, width, height)
            },
            "right": {
                "pupil_points": [],
                "limbus_points": [],
                "eyelid_contour_points": [],
                "glint_points": [],
                "pupil_ellipse": None,
                "limbus_ellipse": None,
                "roi": None,  # (x, y, width, height)
            },
        }

        # Current working data (references to current eye data)
        self.pupil_points = []
        self.limbus_points = []
        self.eyelid_contour_points = []
        self.glint_points = []
        self.pupil_ellipse = None
        self.limbus_ellipse = None
        self.roi = None  # Current eye's ROI

        self.current_annotation = "pupil"
        self.original_pixmap = None
        self.selected_point = None
        self.moving_point = False
        self.panning = False
        self.last_pan_pos = None
        self.pixmap = None
        # Variables for shift-click point movement
        self.shift_pressed = False
        self.last_mouse_pos = None
        self.moving_all_points = False

        # ROI drawing variables
        self.roi_drawing_mode = False
        self.drawing_roi = False
        self.roi_start_pos = None
        self.moving_roi = False
        self.resizing_roi = False
        self.roi_resize_handle = None  # 'tl', 'tr', 'bl', 'br' for corners
        # Which ROI attribute the active drag mutates: "roi", "pupil_roi" or
        # "glint_roi". Reset between drags.
        self._drag_roi_attr: str = "roi"

        # Single-eye mode: when True, the right-eye block is never drawn and
        # current_eye is pinned to "left". Toggled by MainWindow based on the
        # per-project setting or the --single-eye CLI flag.
        self.single_eye_mode = False

        # Manual Threshold live detection: the grayscale numpy array of the
        # currently loaded image (consumed by the off-thread DetectionWorker)
        # and the most recent detection result for overlay rendering. Cleared
        # when the panel is disabled or a new image is loaded.
        self.image_grayscale: np.ndarray | None = None
        # Top-level mode flag — True while the GUI is in Manual Threshold mode.
        # Used by mousePressEvent to suppress the click-to-add-annotation-point
        # flow (pupil / glint / limbus / eyelid) that only applies in Annotate.
        self._is_manual_threshold_mode = False

        self.manual_threshold_detection: dict | None = None

        # Manual Threshold per-image ROIs. ``_mt_active_roi`` names which one
        # (if any) is currently in drag-edit mode — toggled via the panel.
        self.pupil_roi: tuple | None = None
        self.glint_roi: tuple | None = None
        self._mt_active_roi: str | None = None  # None | "pupil" | "glint"

    def setup_colors(self) -> None:
        # Define colors with transparency
        """Set up color definitions for annotations."""
        self.pupil_color = QColor(150, 213, 116, 255)
        self.pupil_select_color = QColor(249, 248, 113, 255)
        self.pupil_ellipse_color = QColor(0, 127, 118, 255)

        self.limbus_color = QColor(194, 149, 188, 255)
        self.limbus_select_color = QColor(249, 178, 208, 255)
        self.limbus_ellipse_color = QColor(139, 122, 162, 255)

        self.eyelid_color = QColor(0, 155, 201, 255)
        self.eyelid_select_color = QColor(0, 189, 194, 255)
        self.eyelid_ellipse_color = QColor(0, 118, 195, 255)

        self.glint_color = QColor(255, 165, 0, 255)  # Orange

        # Manual Threshold overlay uses the same colors as the Annotate-mode
        # manual annotations so the visual language stays consistent: pupil
        # in teal, glints in orange, limbus in dark purple.
        self.mt_pupil_color = self.pupil_ellipse_color
        self.mt_pupil_center_color = self.pupil_color
        self.mt_glint_color = self.glint_color
        self.mt_limbus_color = self.limbus_ellipse_color
        # Manual Threshold ROIs: yellow rectangle for the pupil search region,
        # orange-red for the glint search region.
        self.pupil_roi_color = QColor(255, 220, 0, 255)
        self.glint_roi_color = QColor(255, 100, 60, 255)
        self.glint_select_color = QColor(255, 215, 0, 255)  # Gold

        self.roi_color = QColor(0, 188, 212, 255)  # Cyan

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
        self.eye_data[self.current_eye]["roi"] = self.roi

    def load_current_eye_data(self) -> None:
        """Load the data for the current eye into working variables."""
        self.pupil_points = self.eye_data[self.current_eye]["pupil_points"].copy()
        self.limbus_points = self.eye_data[self.current_eye]["limbus_points"].copy()
        self.eyelid_contour_points = self.eye_data[self.current_eye]["eyelid_contour_points"].copy()
        self.glint_points = self.eye_data[self.current_eye]["glint_points"].copy()
        self.pupil_ellipse = self.eye_data[self.current_eye]["pupil_ellipse"]
        self.limbus_ellipse = self.eye_data[self.current_eye]["limbus_ellipse"]
        self.roi = self.eye_data[self.current_eye]["roi"]

    def switch_eye(self, eye: str) -> None:
        """Switch between left and right eye annotations."""
        if eye not in {"left", "right"}:
            return

        # In single-eye mode the selector is hidden, but defend against
        # programmatic callers requesting "right" — keep the active block
        # pinned to "left" so it stays in sync with what we save.
        if self.single_eye_mode and eye != "left":
            return

        # Save current eye data before switching
        self.save_current_eye_data()

        # Switch to new eye
        self.current_eye = eye

        # Load new eye data
        self.load_current_eye_data()

        # Update display
        self.update_image()
        self.annotation_changed.emit()

    def set_single_eye_mode(self, enabled: bool) -> None:
        """Toggle single-eye mode and re-render."""
        self.single_eye_mode = enabled
        if enabled and self.current_eye != "left":
            self.save_current_eye_data()
            self.current_eye = "left"
            self.load_current_eye_data()
        self.update_image()

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

    def toggle_roi_mode(self) -> None:
        """Toggle ROI drawing mode on/off."""
        self.roi_drawing_mode = not self.roi_drawing_mode
        if not self.roi_drawing_mode:
            self.drawing_roi = False
            self.moving_roi = False
            self.resizing_roi = False
            self.roi_resize_handle = None
        self.update_image()

    def get_roi(self) -> tuple | None:
        """Get the current eye's ROI."""
        return self.roi

    def clear_roi(self) -> None:
        """Clear the current eye's ROI."""
        self.roi = None
        self.save_current_eye_data()
        self.annotation_changed.emit()
        self.update_image()

    # ----- Manual Threshold ROIs -----

    def set_mt_active_roi(self, target: str | None) -> None:
        """Activate draw/edit on the Manual Threshold pupil/glint ROI, or none."""
        if target not in {None, "pupil", "glint"}:
            raise ValueError(f"unknown mt roi target: {target!r}")
        self._mt_active_roi = target
        if target is None:
            self.drawing_roi = False
            self.moving_roi = False
            self.resizing_roi = False
            self.roi_resize_handle = None
        self.update_image()

    def set_pupil_roi(self, roi: tuple | None) -> None:
        """Replace the pupil ROI (without emitting ``pupil_roi_changed``)."""
        self.pupil_roi = roi
        self.update_image()

    def set_glint_roi(self, roi: tuple | None) -> None:
        """Replace the glint ROI (without emitting ``glint_roi_changed``)."""
        self.glint_roi = roi
        self.update_image()

    def clear_pupil_roi(self) -> None:
        """Clear the pupil ROI and emit the change."""
        self.pupil_roi = None
        self.pupil_roi_changed.emit(None)
        self.update_image()

    def clear_glint_roi(self) -> None:
        """Clear the glint ROI and emit the change."""
        self.glint_roi = None
        self.glint_roi_changed.emit(None)
        self.update_image()

    def set_manual_threshold_mode(self, on: bool) -> None:
        """Toggle the top-level Manual Threshold mode flag."""
        self._is_manual_threshold_mode = bool(on)

    def _active_drag_roi_attr(self) -> str | None:
        """Pick the ROI attribute the next drag should mutate, or ``None`` if no drag mode is on."""
        if self.roi_drawing_mode:
            return "roi"
        if self._mt_active_roi == "pupil":
            return "pupil_roi"
        if self._mt_active_roi == "glint":
            return "glint_roi"
        return None

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

    def mousePressEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle mouse press events."""
        if event.button() == Qt.MiddleButton:
            self.panning = True
            self.last_pan_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.LeftButton:
            image_pos = self.get_image_position(event.pos())
            if image_pos:
                # Active ROI drag (Annotate-mode self.roi or one of the
                # Manual Threshold pupil/glint ROIs). Mutually exclusive
                # with the annotation-point flow below.
                roi_attr = self._active_drag_roi_attr()
                if roi_attr is not None:
                    self._drag_roi_attr = roi_attr
                    current = getattr(self, roi_attr)
                    if current:
                        handle = self.get_roi_handle_at_pos(image_pos, current)
                        if handle:
                            self.resizing_roi = True
                            self.roi_resize_handle = handle
                            self.roi_start_pos = image_pos
                            return
                        if self.is_point_in_roi(image_pos, current):
                            self.moving_roi = True
                            self.roi_start_pos = image_pos
                            return
                    # Start drawing a new rectangle, replacing any existing one.
                    self.drawing_roi = True
                    self.roi_start_pos = image_pos
                    setattr(self, roi_attr, None)
                    return

                # Manual Threshold mode owns its own interactions (ROI drag
                # handled above, sliders / auto-detect drive the detection).
                # A plain click outside an ROI should be a no-op here, not a
                # silent manual annotation that the user can't see toggled.
                if self._is_manual_threshold_mode:
                    return

                self.selected_point, selected_annotation = self.find_closest_point_and_type(image_pos)

                if self.selected_point:
                    self.moving_point = True
                    self.last_mouse_pos = image_pos
                    self.moving_all_points = self.shift_pressed

                    if selected_annotation != self.current_annotation:
                        self.current_annotation = selected_annotation
                        self.annotation_type_changed.emit(self.current_annotation)
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
        elif self.drawing_roi or self.moving_roi or self.resizing_roi:
            new_pos = self.get_image_position(event.pos())
            if new_pos and self.roi_start_pos:
                attr = self._drag_roi_attr
                current = getattr(self, attr)
                if self.drawing_roi:
                    x = min(self.roi_start_pos.x(), new_pos.x())
                    y = min(self.roi_start_pos.y(), new_pos.y())
                    w = abs(new_pos.x() - self.roi_start_pos.x())
                    h = abs(new_pos.y() - self.roi_start_pos.y())
                    setattr(self, attr, (x, y, w, h))
                elif self.moving_roi and current:
                    delta_x = new_pos.x() - self.roi_start_pos.x()
                    delta_y = new_pos.y() - self.roi_start_pos.y()
                    x, y, w, h = current
                    setattr(self, attr, (x + delta_x, y + delta_y, w, h))
                    self.roi_start_pos = new_pos
                elif self.resizing_roi and current:
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
                    setattr(self, attr, (x, y, max(10, w), max(10, h)))
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
            if self.drawing_roi or self.moving_roi or self.resizing_roi:
                self.drawing_roi = False
                self.moving_roi = False
                self.resizing_roi = False
                self.roi_resize_handle = None
                attr = self._drag_roi_attr
                current = getattr(self, attr)
                if attr == "roi":
                    if current:
                        self.save_current_eye_data()
                        self.annotation_changed.emit()
                elif attr == "pupil_roi":
                    self.pupil_roi_changed.emit(current)
                elif attr == "glint_roi":
                    self.glint_roi_changed.emit(current)
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
        (for the off-thread Manual Threshold worker). Failing to decode the
        grayscale array is non-fatal — the pixmap path is the user-visible
        source of truth and Manual Threshold mode just stays disabled.
        """
        self.original_pixmap = QPixmap(image_path)
        if self.original_pixmap.isNull():
            return False
        self.image_grayscale = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        self.manual_threshold_detection = None
        # Manual Threshold ROIs are per-image; Phase A6 will restore them from
        # the annotation JSON. Until then, clear on every load.
        self.pupil_roi = None
        self.glint_roi = None
        self.pupil_points = []
        self.limbus_points = []
        self.pupil_ellipse = None
        self.limbus_ellipse = None
        self.reset_undo_stack()  # This will now save the empty state
        self.update_image()
        self.image_loaded.emit()
        return True

    def get_current_image_grayscale(self) -> np.ndarray | None:
        """Return the grayscale numpy view of the current image (or None)."""
        return self.image_grayscale

    def set_manual_threshold_detection(self, detection: dict | None) -> None:
        """Store and render the latest live-detection result from the worker.

        ``detection`` carries ``pupil_ellipse``, ``pupil_center``, and
        ``glints`` keys (the JSON-serialisable subset the worker emits).
        Passing ``None`` clears the overlay.
        """
        self.manual_threshold_detection = detection
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

    def update_image(self) -> None:
        """Update the displayed image with annotations."""
        if self.original_pixmap is None or self.original_pixmap.isNull():
            return
        scaled_pixmap = self.original_pixmap.scaled(
            self.original_pixmap.size() * self.factor,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.pixmap = QPixmap(scaled_pixmap.size())
        self.pixmap.fill(Qt.transparent)
        painter = QPainter(self.pixmap)
        painter.drawPixmap(0, 0, scaled_pixmap)

        # Draw the active eye's annotations. In single-eye mode the right
        # block is unused and we skip it to avoid drawing stale data carried
        # over from a multi-eye file loaded earlier.
        self.draw_eye_annotations(painter, "left")
        if not self.single_eye_mode:
            self.draw_eye_annotations(painter, "right")

        # Draw ROI if it exists and we're in ROI mode or it's defined
        if self.roi:
            self.draw_roi(painter)

        # Manual Threshold pupil/glint ROIs (always visible when set).
        if self.pupil_roi:
            self._draw_named_roi(painter, self.pupil_roi, self.pupil_roi_color, "pupil")
        if self.glint_roi:
            self._draw_named_roi(painter, self.glint_roi, self.glint_roi_color, "glint")

        # Manual Threshold live detection overlay (on top of everything else
        # so the user can see it against any manual annotation underneath).
        if self.manual_threshold_detection is not None:
            self.draw_manual_threshold_overlay(painter)

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

                # In single-eye mode the L/R distinction is meaningless, so
                # the per-point eye label is suppressed entirely.
                if not self.single_eye_mode:
                    font = painter.font()
                    font.setPointSize(8)
                    painter.setFont(font)
                    painter.setPen(QPen(color, 1, Qt.SolidLine))
                    text_pos = QPointF(scaled_point.x() + 6, scaled_point.y() - 4)
                    painter.drawText(text_pos, eye_label)

    def draw_ellipses_for_eye(self, painter: QPainter, eye_data: dict) -> None:
        """Draw fitted ellipses for a specific eye."""
        if eye_data["pupil_ellipse"]:
            painter.setPen(QPen(self.pupil_ellipse_color, 1, Qt.SolidLine))
            self.draw_single_ellipse(painter, eye_data["pupil_ellipse"])
        if eye_data["limbus_ellipse"]:
            painter.setPen(QPen(self.limbus_ellipse_color, 1, Qt.SolidLine))
            self.draw_single_ellipse(painter, eye_data["limbus_ellipse"])

    def draw_roi(self, painter: QPainter) -> None:
        """Draw the ROI rectangle with dashed lines and corner handles."""
        if not self.roi:
            return

        x, y, w, h = self.roi
        scaled_x = x * self.factor
        scaled_y = y * self.factor
        scaled_w = w * self.factor
        scaled_h = h * self.factor

        # Draw dashed rectangle
        pen = QPen(self.roi_color, 2, Qt.DashLine)
        painter.setPen(pen)
        painter.drawRect(int(scaled_x), int(scaled_y), int(scaled_w), int(scaled_h))

        # Draw corner handles if in ROI drawing mode
        if self.roi_drawing_mode:
            handle_size = 8
            painter.setPen(QPen(self.roi_color, 2, Qt.SolidLine))
            painter.setBrush(self.roi_color)

            # Draw corner handles
            corners = [
                (scaled_x, scaled_y),  # top-left
                (scaled_x + scaled_w, scaled_y),  # top-right
                (scaled_x, scaled_y + scaled_h),  # bottom-left
                (scaled_x + scaled_w, scaled_y + scaled_h),  # bottom-right
            ]
            for cx, cy in corners:
                painter.drawRect(
                    int(cx - handle_size / 2),
                    int(cy - handle_size / 2),
                    handle_size,
                    handle_size,
                )

    def _draw_named_roi(self, painter: QPainter, roi: tuple, color: QColor, name: str) -> None:
        """Render a Manual Threshold ROI with dashed lines + corner handles when active."""
        x, y, w, h = roi
        scaled_x = x * self.factor
        scaled_y = y * self.factor
        scaled_w = w * self.factor
        scaled_h = h * self.factor

        painter.setPen(QPen(color, 2, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(int(scaled_x), int(scaled_y), int(scaled_w), int(scaled_h))

        # Corner handles are only drawn when this ROI is the active drag target.
        if self._mt_active_roi == name:
            handle_size = 8
            painter.setPen(QPen(color, 2, Qt.SolidLine))
            painter.setBrush(color)
            corners = [
                (scaled_x, scaled_y),
                (scaled_x + scaled_w, scaled_y),
                (scaled_x, scaled_y + scaled_h),
                (scaled_x + scaled_w, scaled_y + scaled_h),
            ]
            for cx, cy in corners:
                painter.drawRect(
                    int(cx - handle_size / 2),
                    int(cy - handle_size / 2),
                    handle_size,
                    handle_size,
                )

    def draw_manual_threshold_overlay(self, painter: QPainter) -> None:
        """Render the live detection from the Manual Threshold worker.

        Expects ``self.manual_threshold_detection`` shaped like the
        ``DetectionWorker.detection_ready`` payload: ``pupil_ellipse``
        ``((cx, cy), (w, h), angle)``, ``pupil_center`` ``(cx, cy)``, and
        ``glints`` ``[(gx, gy), ...]``. Coordinates are in original image
        space and are scaled by ``self.factor`` to match the displayed pixmap.
        """
        det = self.manual_threshold_detection
        pupil_ellipse = det.get("pupil_ellipse")
        pupil_center = det.get("pupil_center")
        glints = det.get("glints") or []

        # Pupil ellipse: solid teal outline, matches the Annotate-mode style.
        if pupil_ellipse is not None:
            (cx, cy), (w, h), angle = pupil_ellipse
            painter.save()
            painter.setPen(QPen(self.mt_pupil_color, 1, Qt.SolidLine))
            painter.setBrush(Qt.NoBrush)
            painter.translate(QPointF(cx * self.factor, cy * self.factor))
            painter.rotate(angle)
            painter.drawEllipse(QPointF(0, 0), (w / 2) * self.factor, (h / 2) * self.factor)
            painter.restore()

        # Limbus: solid dark-purple circle around the limbus.
        limbus = det.get("limbus")
        if limbus is not None:
            lcx, lcy = limbus["center"]
            lr = limbus["radius"]
            painter.save()
            painter.setPen(QPen(self.mt_limbus_color, 1, Qt.SolidLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(
                QPointF(lcx * self.factor, lcy * self.factor),
                lr * self.factor,
                lr * self.factor,
            )
            painter.restore()

        # Pupil center marker: small filled dot like the manual pupil points.
        if pupil_center is not None:
            pcx, pcy = pupil_center
            scaled = QPointF(pcx * self.factor, pcy * self.factor)
            painter.setBrush(self.mt_pupil_center_color)
            painter.setPen(QPen(self.mt_pupil_center_color, 3, Qt.SolidLine))
            painter.drawEllipse(scaled, 1.5, 1.5)

        # Glints: small filled orange dots, same shape as manual glint points.
        painter.setBrush(self.mt_glint_color)
        painter.setPen(QPen(self.mt_glint_color, 3, Qt.SolidLine))
        for gx, gy in glints:
            painter.drawEllipse(QPointF(gx * self.factor, gy * self.factor), 1.5, 1.5)

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
        """Find the closest point and its annotation type."""
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
            if point:
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

    def is_point_in_roi(self, point: QPointF, roi: tuple | None = None) -> bool:
        """Check if a point is inside the given rectangle (defaults to ``self.roi``)."""
        rect = roi if roi is not None else self.roi
        if not rect:
            return False
        x, y, w, h = rect
        return x <= point.x() <= x + w and y <= point.y() <= y + h

    def get_roi_handle_at_pos(self, point: QPointF, roi: tuple | None = None) -> str | None:
        """Get the corner-handle name (tl/tr/bl/br) at ``point`` for the given rectangle."""
        rect = roi if roi is not None else self.roi
        if not rect:
            return None

        x, y, w, h = rect
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
