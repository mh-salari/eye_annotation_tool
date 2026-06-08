"""Handler for keyboard shortcuts."""

from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import QShortcut

if TYPE_CHECKING:
    from .main_window import MainWindow


class ShortcutHandler:
    """Manages keyboard shortcuts for the application."""

    def __init__(self, main_window: "MainWindow") -> None:
        """Initialize the ShortcutHandler."""
        self.main_window = main_window

    def setup_shortcuts(self) -> None:
        """Set up keyboard shortcuts for the application."""
        # Undo / redo — one shared history across manual points and detector
        # settings. QKeySequence picks the platform-correct keys (Cmd+Z / Ctrl+Z,
        # Cmd+Shift+Z / Ctrl+Y, etc.).
        undo_shortcut = QShortcut(QKeySequence.Undo, self.main_window)
        undo_shortcut.activated.connect(self.main_window.undo_coordinator.undo)
        redo_shortcut = QShortcut(QKeySequence.Redo, self.main_window)
        redo_shortcut.activated.connect(self.main_window.undo_coordinator.redo)

        # Copy / paste the current eye's detector settings between images
        # (Cmd+C / Cmd+V on macOS, Ctrl+C / Ctrl+V elsewhere).
        copy_shortcut = QShortcut(QKeySequence.Copy, self.main_window)
        copy_shortcut.activated.connect(self.main_window.copy_settings)
        paste_shortcut = QShortcut(QKeySequence.Paste, self.main_window)
        paste_shortcut.activated.connect(self.main_window.paste_settings)

        # Save shortcut
        save_shortcut = QShortcut(QKeySequence.Save, self.main_window)
        save_shortcut.activated.connect(self.main_window.annotation_controller.save_annotations)

        # Next image shortcut
        next_image_shortcut = QShortcut(QKeySequence(Qt.Key_Right), self.main_window)
        next_image_shortcut.activated.connect(self.main_window.navigation_controller.next_image)

        # Previous image shortcut
        prev_image_shortcut = QShortcut(QKeySequence(Qt.Key_Left), self.main_window)
        prev_image_shortcut.activated.connect(self.main_window.navigation_controller.prev_image)

        # Toggle between pupil and limbus
        toggle_shortcut = QShortcut(QKeySequence(Qt.Key_Tab), self.main_window)
        toggle_shortcut.activated.connect(self.toggle_annotation_type)

    def toggle_annotation_type(self) -> None:
        """Toggle between different annotation types."""
        current_type = self.main_window.annotation_controls.get_current_annotation_type()
        if current_type == "pupil":
            new_type = "limbus"
        elif current_type == "limbus":
            new_type = "eyelid_contour"
        else:
            new_type = "pupil"

        self.main_window.annotation_controls.set_current_annotation(new_type)
        self.main_window.annotation_controls.annotation_changed.emit(new_type)
