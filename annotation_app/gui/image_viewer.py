"""Image viewer widget for displaying and annotating eye images."""

from collections import deque

import numpy as np
from PyQt5.QtCore import QEvent, QPoint, QPointF, QSizeF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeyEvent, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QLabel, QMessageBox, QScrollArea, QVBoxLayout, QWidget

from ..utils.image_processing import find_closest_point, fit_ellipse


class ImageViewer(QWidget):
    """Widget for viewing and annotating eye images with pupil, iris, eyelid, and glint markers."""

    annotation_changed = pyqtSignal()
    annotation_type_changed = pyqtSignal(str)

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
        self.pupil_points = []
        self.iris_points = []
        self.eyelid_contour_points = []
        self.glint_points = []
        self.pupil_ellipse = None
        self.iris_ellipse = None
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

    def setup_colors(self) -> None:
        # Define colors with transparency
        """Set up color definitions for annotations."""
        self.pupil_color = QColor(150, 213, 116, 255)
        self.pupil_select_color = QColor(249, 248, 113, 255)
        self.pupil_ellipse_color = QColor(0, 127, 118, 255)

        self.iris_color = QColor(194, 149, 188, 255)
        self.iris_select_color = QColor(249, 178, 208, 255)
        self.iris_ellipse_color = QColor(139, 122, 162, 255)

        self.eyelid_color = QColor(0, 155, 201, 255)
        self.eyelid_select_color = QColor(0, 189, 194, 255)
        self.eyelid_ellipse_color = QColor(0, 118, 195, 255)

        self.glint_color = QColor(255, 165, 0, 255)  # Orange
        self.glint_select_color = QColor(255, 215, 0, 255)  # Gold

    def setup_undo_system(self) -> None:
        """Initialize the undo/redo system."""
        self.undo_stack = deque(maxlen=10)
        self.undo_index = -1

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
            "iris_points": self.iris_points.copy(),
            "eyelid_contour_points": self.eyelid_contour_points.copy(),
            "glint_points": self.glint_points.copy(),
            "pupil_ellipse": self.pupil_ellipse,
            "iris_ellipse": self.iris_ellipse,
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
            self.iris_points = state["iris_points"].copy()
            self.eyelid_contour_points = state.get("eyelid_contour_points", []).copy()
            self.glint_points = state.get("glint_points", []).copy()
            self.pupil_ellipse = state["pupil_ellipse"]
            self.iris_ellipse = state["iris_ellipse"]
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
            elif self.current_annotation == "iris":
                points = self.iris_points
            elif self.current_annotation == "eyelid_contour":
                points = self.eyelid_contour_points
            else:  # glint
                points = self.glint_points

            if self.selected_point in points:
                points.remove(self.selected_point)
                self.selected_point = None
                self.save_state()
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
                elif self.current_annotation == "iris":
                    self.iris_points.append(image_pos)
                elif self.current_annotation == "eyelid_contour":
                    self.eyelid_contour_points.append(image_pos)
                else:  # glint
                    self.glint_points.append(image_pos)

                self.save_state()
                self.annotation_changed.emit()
                self.update_image()

    def mouseMoveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle mouse move events."""
        if self.panning:
            delta = event.pos() - self.last_pan_pos
            self.scroll_area.horizontalScrollBar().setValue(self.scroll_area.horizontalScrollBar().value() - delta.x())
            self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().value() - delta.y())
            self.last_pan_pos = event.pos()
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
                    elif self.current_annotation == "iris":
                        self.move_points_by_delta(self.iris_points, delta_x, delta_y)
                    elif self.current_annotation == "eyelid_contour":
                        self.move_points_by_delta(self.eyelid_contour_points, delta_x, delta_y)
                    else:  # glint
                        self.move_points_by_delta(self.glint_points, delta_x, delta_y)
                # Move only the selected point
                elif self.current_annotation == "pupil":
                    index = self.pupil_points.index(self.selected_point)
                    self.pupil_points[index] = new_pos
                elif self.current_annotation == "iris":
                    index = self.iris_points.index(self.selected_point)
                    self.iris_points[index] = new_pos
                elif self.current_annotation == "eyelid_contour":
                    index = self.eyelid_contour_points.index(self.selected_point)
                    self.eyelid_contour_points[index] = new_pos
                else:  # glint
                    index = self.glint_points.index(self.selected_point)
                    self.glint_points[index] = new_pos

                self.selected_point = new_pos
                self.last_mouse_pos = new_pos
                self.update_image()

    def mouseReleaseEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle mouse release events."""
        if event.button() == Qt.MiddleButton:
            self.panning = False
            self.setCursor(Qt.ArrowCursor)
        elif event.button() == Qt.LeftButton:
            self.moving_point = False
            if self.selected_point:
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
        """Load an image from the given path."""
        self.original_pixmap = QPixmap(image_path)
        if self.original_pixmap.isNull():
            return False
        self.pupil_points = []
        self.iris_points = []
        self.pupil_ellipse = None
        self.iris_ellipse = None
        self.reset_undo_stack()  # This will now save the empty state
        self.update_image()
        return True

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
        self.draw_points(painter)
        self.draw_ellipses(painter)
        painter.end()
        self.image_label.setPixmap(self.pixmap)
        self.image_label.resize(self.pixmap.size())

    def fit_annotation(self) -> bool:
        """Fit an ellipse to the current annotation points."""
        if self.current_annotation in {"pupil", "iris"}:
            return self.fit_ellipse()
        return False

    def draw_points(self, painter: QPainter) -> None:
        """Draw annotation points on the image."""
        for points, color, annotation_type in [
            (self.pupil_points, self.pupil_color, "pupil"),
            (self.iris_points, self.iris_color, "iris"),
            (self.eyelid_contour_points, self.eyelid_color, "eyelid_contour"),
            (self.glint_points, self.glint_color, "glint"),
        ]:
            for point in points:
                scaled_point = QPointF(point.x() * self.factor, point.y() * self.factor)
                if point == self.selected_point and self.current_annotation == annotation_type:
                    if annotation_type == "pupil":
                        painter.setPen(QPen(self.pupil_select_color, 3, Qt.SolidLine))
                    elif annotation_type == "iris":
                        painter.setPen(QPen(self.iris_select_color, 3, Qt.SolidLine))
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
        if self.iris_ellipse:
            painter.setPen(QPen(self.iris_ellipse_color, 1, Qt.SolidLine))
            self.draw_single_ellipse(painter, self.iris_ellipse)

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
        iris_point = find_closest_point(self.iris_points, pos, self.factor)
        eyelid_point = find_closest_point(self.eyelid_contour_points, pos, self.factor)
        glint_point = find_closest_point(self.glint_points, pos, self.factor)

        closest_point = None
        closest_type = None
        min_dist = float("inf")

        for point, point_type in [
            (pupil_point, "pupil"),
            (iris_point, "iris"),
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
        self.annotation_changed.emit()
        self.update_image()

    def clear_iris_points(self) -> None:
        """Clear all iris annotation points."""
        self.iris_points = []
        self.iris_ellipse = None
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def clear_iris_ellipse(self) -> None:
        """Clear the fitted iris ellipse."""
        self.iris_ellipse = None
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def clear_pupil_ellipse(self) -> None:
        """Clear the fitted pupil ellipse."""
        self.pupil_ellipse = None
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def clear_eyelid_points(self) -> None:
        """Clear all eyelid contour points."""
        self.eyelid_contour_points = []
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def clear_glint_points(self) -> None:
        """Clear all glint points."""
        self.glint_points = []
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def clear_all(self) -> None:
        """Clear all annotations."""
        self.clear_pupil_points()
        self.clear_iris_points()
        self.clear_eyelid_points()
        self.clear_glint_points()

    def get_annotation_data(self) -> dict:
        """Get all annotation data as a dictionary."""
        return {
            "pupil_points": self.pupil_points,
            "iris_points": self.iris_points,
            "eyelid_contour_points": self.eyelid_contour_points,
            "glint_points": self.glint_points,
            "pupil_ellipse": self.pupil_ellipse,
            "iris_ellipse": self.iris_ellipse,
        }

    def set_annotation_data(self, data: dict) -> None:
        """Set annotation data from a dictionary."""
        self.pupil_points = data.get("pupil_points", [])
        self.iris_points = data.get("iris_points", [])
        self.eyelid_contour_points = data.get("eyelid_contour_points", [])
        self.glint_points = data.get("glint_points", [])
        self.pupil_ellipse = data.get("pupil_ellipse")
        self.iris_ellipse = data.get("iris_ellipse")
        self.reset_undo_stack(initial_state=self.get_current_state())
        self.update_image()

    def fit_ellipse(self) -> bool:
        """Fit an ellipse to annotation points."""
        points = self.pupil_points if self.current_annotation == "pupil" else self.iris_points
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
                self.iris_ellipse = (center, size, angle)
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
        elif self.current_annotation == "iris":
            self.clear_iris_ellipse()

    @staticmethod
    def move_points_by_delta(points: list[QPointF], delta_x: float, delta_y: float) -> None:
        """Helper method to move all points in a list by a given delta."""
        for i in range(len(points)):
            points[i] = QPointF(points[i].x() + delta_x, points[i].y() + delta_y)
