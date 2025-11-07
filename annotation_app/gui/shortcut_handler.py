from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import QShortcut


class ShortcutHandler:
    def __init__(self, main_window):
        self.main_window = main_window

    def setup_shortcuts(self):
        # Undo shortcut
        undo_shortcut = QShortcut(QKeySequence.Undo, self.main_window)
        undo_shortcut.activated.connect(self.main_window.image_viewer.undo)

        # Save shortcut
        save_shortcut = QShortcut(QKeySequence.Save, self.main_window)
        save_shortcut.activated.connect(self.main_window.annotation_controller.save_annotations)

        # Next image shortcut
        next_image_shortcut = QShortcut(QKeySequence(Qt.Key_Right), self.main_window)
        next_image_shortcut.activated.connect(self.main_window.navigation_controller.next_image)

        # Previous image shortcut
        prev_image_shortcut = QShortcut(QKeySequence(Qt.Key_Left), self.main_window)
        prev_image_shortcut.activated.connect(self.main_window.navigation_controller.prev_image)

        # Toggle between pupil and iris
        toggle_shortcut = QShortcut(QKeySequence(Qt.Key_Tab), self.main_window)
        toggle_shortcut.activated.connect(self.toggle_annotation_type)

    def toggle_annotation_type(self):
        current_type = self.main_window.annotation_controls.get_current_annotation_type()
        if current_type == "pupil":
            new_type = "iris"
        elif current_type == "iris":
            new_type = "eyelid_contour"
        else:
            new_type = "pupil"

        self.main_window.annotation_controls.set_current_annotation(new_type)
        self.main_window.annotation_controls.annotation_changed.emit(new_type)
