from PyQt5.QtWidgets import QLabel, QScrollArea, QWidget, QVBoxLayout, QMessageBox
from PyQt5.QtGui import QPixmap, QPainter, QPen, QColor, QKeyEvent
from PyQt5.QtCore import Qt, QPointF, QSizeF, pyqtSignal, QEvent, QPoint
from collections import deque
from ..utils.image_processing import find_closest_point, fit_ellipse
import numpy as np


class ImageViewer(QWidget):
    annotation_changed = pyqtSignal()
    annotation_type_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        self.setup_variables()
        self.setup_undo_system()
        self.setup_colors()

        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_MouseTracking, True)
        self.setMouseTracking(True)

    def setup_ui(self):
        layout = QVBoxLayout()
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.image_label)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.scroll_area)
        self.setLayout(layout)

        self.scroll_area.viewport().installEventFilter(self)

    def setup_variables(self):
        self.factor = 1.0
        self.pupil_points = []
        self.iris_points = []
        self.eyelid_contour_points = []
        self.pupil_ellipse = None
        self.iris_ellipse = None
        self.current_annotation = "pupil"
        self.original_pixmap = None
        self.selected_point = None
        self.moving_point = False
        self.panning = False
        self.last_pan_pos = None
        self.pixmap = None

    def setup_colors(self):
        # Define colors with transparency
        self.pupil_color = QColor(150, 213, 116, 255)
        self.pupil_select_color = QColor(249, 248, 113, 255)
        self.pupil_ellipse_color = QColor(0, 127, 118, 255)

        self.iris_color = QColor(194, 149, 188, 255)
        self.iris_select_color = QColor(249, 178, 208, 255)
        self.iris_ellipse_color = QColor(139, 122, 162, 255)

        self.eyelid_color = QColor(0, 155, 201, 255)
        self.eyelid_select_color = QColor(0, 189, 194, 255)
        self.eyelid_ellipse_color = QColor(0, 118, 195, 255)

    def setup_undo_system(self):
        self.undo_stack = deque(maxlen=10)
        self.undo_index = -1

    def reset_undo_stack(self, initial_state=None):
        self.undo_stack.clear()
        self.undo_index = -1
        if initial_state is None:
            initial_state = self.get_current_state()
        self.undo_stack.append(initial_state)
        self.undo_index = 0

    def get_current_state(self):
        return {
            "pupil_points": self.pupil_points.copy(),
            "iris_points": self.iris_points.copy(),
            "pupil_ellipse": self.pupil_ellipse,
            "iris_ellipse": self.iris_ellipse,
        }

    def save_state(self):
        state = self.get_current_state()
        if self.undo_index < len(self.undo_stack) - 1:
            # If we're not at the end of the stack, remove future states
            self.undo_stack = deque(
                list(self.undo_stack)[: self.undo_index + 1], maxlen=5
            )
        self.undo_stack.append(state)
        self.undo_index = len(self.undo_stack) - 1

    def can_undo(self):
        return self.undo_index > 0

    def undo(self):
        if self.can_undo():
            self.undo_index -= 1
            state = self.undo_stack[self.undo_index]
            self.pupil_points = state["pupil_points"].copy()
            self.iris_points = state["iris_points"].copy()
            self.pupil_ellipse = state["pupil_ellipse"]
            self.iris_ellipse = state["iris_ellipse"]
            self.update_image()
            self.annotation_changed.emit()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Delete:
            self.delete_selected_point()
        else:
            super().keyPressEvent(event)

    def delete_selected_point(self):
        if self.selected_point:
            if self.current_annotation == "pupil":
                points = self.pupil_points
            elif self.current_annotation == "iris":
                points = self.iris_points
            else:  # eyelid contour
                points = self.eyelid_contour_points
                # Clear the fitted curve when modifying points

            if self.selected_point in points:
                points.remove(self.selected_point)
                self.selected_point = None
                self.save_state()
                self.annotation_changed.emit()
                self.update_image()

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.panning = True
            self.last_pan_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.LeftButton:
            image_pos = self.get_image_position(event.pos())
            if image_pos:
                self.selected_point, selected_annotation = (
                    self.find_closest_point_and_type(image_pos)
                )

                if self.selected_point:
                    self.moving_point = True
                    if selected_annotation != self.current_annotation:
                        self.current_annotation = selected_annotation
                        self.annotation_type_changed.emit(self.current_annotation)
                else:
                    if self.current_annotation == "pupil":
                        self.pupil_points.append(image_pos)
                    elif self.current_annotation == "iris":
                        self.iris_points.append(image_pos)
                    else:  # eyelid contour
                        self.eyelid_contour_points.append(image_pos)

                self.save_state()
                self.annotation_changed.emit()
                self.update_image()

    def mouseMoveEvent(self, event):
        if self.panning:
            delta = event.pos() - self.last_pan_pos
            self.scroll_area.horizontalScrollBar().setValue(
                self.scroll_area.horizontalScrollBar().value() - delta.x()
            )
            self.scroll_area.verticalScrollBar().setValue(
                self.scroll_area.verticalScrollBar().value() - delta.y()
            )
            self.last_pan_pos = event.pos()
        elif self.moving_point and self.selected_point:
            new_pos = self.get_image_position(event.pos())
            if new_pos:
                if self.current_annotation == "pupil":
                    index = self.pupil_points.index(self.selected_point)
                    self.pupil_points[index] = new_pos
                elif self.current_annotation == "iris":
                    index = self.iris_points.index(self.selected_point)
                    self.iris_points[index] = new_pos
                else:  # eyelid contour
                    index = self.eyelid_contour_points.index(self.selected_point)
                    self.eyelid_contour_points[index] = new_pos
                self.selected_point = new_pos
                self.update_image()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.panning = False
            self.setCursor(Qt.ArrowCursor)
        elif event.button() == Qt.LeftButton:
            self.moving_point = False
            if self.selected_point:
                self.save_state()
                self.annotation_changed.emit()

    def wheelEvent(self, event):
        if event.modifiers() == Qt.ControlModifier:
            zoom_in = event.angleDelta().y() > 0
            self.zoom(zoom_in, event.position().toPoint())
            event.accept()  # Prevent the event from being passed to the parent widget
        else:
            # Only allow scrolling when not zooming
            super().wheelEvent(event)

    def load_image(self, image_path):
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

    def eventFilter(self, source, event):
        if (
            source == self.scroll_area.viewport()
            and event.type() == QEvent.Wheel
            and event.modifiers() == Qt.ControlModifier
        ):

            zoom_in = event.angleDelta().y() > 0
            self.zoom(zoom_in, event.position().toPoint())
            return True  # Event handled, don't propagate further

        return super().eventFilter(source, event)  # Propagate other events

    def zoom(self, zoom_in, pos):
        old_factor = self.factor
        if zoom_in:
            self.factor *= 1.1
        else:
            self.factor /= 1.1

        self.factor = max(0.1, min(25, self.factor))  # Limit zoom level

        # Calculate the new scroll position to keep the point under the cursor fixed
        viewport_center = self.scroll_area.viewport().rect().center()
        scene_pos = self.scroll_area.mapToGlobal(viewport_center) - self.mapToGlobal(
            QPoint(0, 0)
        )
        delta = pos - scene_pos

        h_bar = self.scroll_area.horizontalScrollBar()
        v_bar = self.scroll_area.verticalScrollBar()

        h_bar.setValue(int(h_bar.value() + delta.x() * (self.factor / old_factor - 1)))
        v_bar.setValue(int(v_bar.value() + delta.y() * (self.factor / old_factor - 1)))

        self.update_image()

    def update_image(self):
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

    def fit_annotation(self):
        if self.current_annotation in ["pupil", "iris"]:
            return self.fit_ellipse()
        return False

    def draw_points(self, painter):
        for points, color, annotation_type in [
            (self.pupil_points, self.pupil_color, "pupil"),
            (self.iris_points, self.iris_color, "iris"),
            (self.eyelid_contour_points, self.eyelid_color, "eyelid_contour"),
        ]:
            for point in points:
                scaled_point = QPointF(point.x() * self.factor, point.y() * self.factor)
                if (
                    point == self.selected_point
                    and self.current_annotation == annotation_type
                ):
                    if annotation_type == "pupil":
                        painter.setPen(QPen(self.pupil_select_color, 3, Qt.SolidLine))
                    elif annotation_type == "iris":
                        painter.setPen(QPen(self.iris_select_color, 3, Qt.SolidLine))
                    else:  # eyelid contour
                        painter.setPen(QPen(self.eyelid_select_color, 3, Qt.SolidLine))
                else:
                    painter.setPen(QPen(color, 3, Qt.SolidLine))
                painter.drawEllipse(scaled_point, 1.5, 1.5)

    def draw_ellipses(self, painter):
        if self.pupil_ellipse:
            painter.setPen(QPen(self.pupil_ellipse_color, 1, Qt.SolidLine))
            self.draw_single_ellipse(painter, self.pupil_ellipse)
        if self.iris_ellipse:
            painter.setPen(QPen(self.iris_ellipse_color, 1, Qt.SolidLine))
            self.draw_single_ellipse(painter, self.iris_ellipse)

    def draw_single_ellipse(self, painter, ellipse):
        if ellipse is None:
            return
        center, size, angle = ellipse
        scaled_center = QPointF(center.x() * self.factor, center.y() * self.factor)
        scaled_size = QSizeF(size.width() * self.factor, size.height() * self.factor)
        painter.save()
        painter.translate(scaled_center)
        painter.rotate(angle)
        painter.drawEllipse(
            QPointF(0, 0), scaled_size.width() / 2, scaled_size.height() / 2
        )
        painter.restore()

    def find_closest_point_and_type(self, pos):
        pupil_point = find_closest_point(self.pupil_points, pos, self.factor)
        iris_point = find_closest_point(self.iris_points, pos, self.factor)
        eyelid_point = find_closest_point(self.eyelid_contour_points, pos, self.factor)

        closest_point = None
        closest_type = None
        min_dist = float("inf")

        for point, point_type in [
            (pupil_point, "pupil"),
            (iris_point, "iris"),
            (eyelid_point, "eyelid_contour"),
        ]:
            if point:
                dist = (point.x() - pos.x()) ** 2 + (point.y() - pos.y()) ** 2
                if dist < min_dist:
                    min_dist = dist
                    closest_point = point
                    closest_type = point_type

        return closest_point, closest_type

    def get_image_position(self, pos):
        if self.pixmap:
            widget_pos = self.scroll_area.mapFrom(self, pos)
            image_pos = self.image_label.mapFrom(self.scroll_area, widget_pos)
            scaled_pos = QPointF(
                image_pos.x() / self.factor, image_pos.y() / self.factor
            )
            if (
                0 <= scaled_pos.x() < self.original_pixmap.width()
                and 0 <= scaled_pos.y() < self.original_pixmap.height()
            ):
                return scaled_pos
        return None

    def set_current_annotation(self, annotation_type):
        if self.current_annotation != annotation_type:
            self.current_annotation = annotation_type
            self.annotation_type_changed.emit(
                self.current_annotation
            )  # Emit the new signal
        self.annotation_changed.emit()

    def clear_pupil_points(self):
        self.pupil_points = []
        self.pupil_ellipse = None
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def clear_iris_points(self):
        self.iris_points = []
        self.iris_ellipse = None
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def clear_iris_ellipse(self):
        self.iris_ellipse = None
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def clear_pupil_ellipse(self):
        self.pupil_ellipse = None
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def clear_eyelid_points(self):
        self.eyelid_contour_points = []
        self.save_state()
        self.annotation_changed.emit()
        self.update_image()

    def clear_all(self):
        self.clear_pupil_points()
        self.clear_iris_points()
        self.clear_eyelid_points()

    def get_annotation_data(self):
        return {
            "pupil_points": self.pupil_points,
            "iris_points": self.iris_points,
            "eyelid_contour_points": self.eyelid_contour_points,
            "pupil_ellipse": self.pupil_ellipse,
            "iris_ellipse": self.iris_ellipse,
        }

    def set_annotation_data(self, data):
        self.pupil_points = data.get("pupil_points", [])
        self.iris_points = data.get("iris_points", [])
        self.eyelid_contour_points = data.get("eyelid_contour_points", [])
        self.pupil_ellipse = data.get("pupil_ellipse")
        self.iris_ellipse = data.get("iris_ellipse")
        self.reset_undo_stack(initial_state=self.get_current_state())
        self.update_image()

    def fit_ellipse(self):
        points = (
            self.pupil_points
            if self.current_annotation == "pupil"
            else self.iris_points
        )
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
        elif len(points) != 0:
            QMessageBox.warning(
                self,
                "Warning",
                f"At least 5 points are required to fit the {self.current_annotation} ellipse.",
            )
        return False

    def clear_selected_ellipse(self):
        if self.current_annotation == "pupil":
            self.clear_pupil_ellipse()
        elif self.current_annotation == "iris":
            self.clear_iris_ellipse()
