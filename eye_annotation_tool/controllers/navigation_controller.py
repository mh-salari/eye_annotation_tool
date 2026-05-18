"""Controller for managing image navigation."""

from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QListWidgetItem, QMessageBox

if TYPE_CHECKING:
    from ..gui.main_window import MainWindow


class NavigationController:
    """Handles navigation between images in the annotation tool."""

    def __init__(self, main_window: "MainWindow") -> None:
        """Initialize the NavigationController.

        Args:
            main_window: Reference to the main application window.

        """
        self.main_window = main_window

    def _handle_unsaved_before_switch(self) -> bool:
        """Save / prompt / cancel based on autosave; return True to proceed with the switch.

        When autosave is enabled, every navigation persists the current image
        regardless of whether the user touched it — auto-detector results are
        produced automatically on image load, and skipping the save would
        leave them out of the on-disk annotation.
        """
        if self.main_window.autosave_enabled:
            self.main_window.annotation_controller.save_current_annotations(silent=True)
            return True
        if not self.main_window.annotation_modified:
            return True
        reply = self.show_save_dialog()
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Yes:
            self.main_window.save_current_annotations()
        return True

    def next_image(self) -> None:
        """Navigate to the next image in the list."""
        if self.main_window.current_image_index < len(self.main_window.image_paths) - 1:
            if not self._handle_unsaved_before_switch():
                return
            self.main_window.current_image_index += 1
            self.main_window.load_current_image()
            self.main_window.image_list_widget.setCurrentRow(self.main_window.current_image_index)

    def prev_image(self) -> None:
        """Navigate to the previous image in the list."""
        if self.main_window.current_image_index > 0:
            if not self._handle_unsaved_before_switch():
                return
            self.main_window.current_image_index -= 1
            self.main_window.load_current_image()
            self.main_window.image_list_widget.setCurrentRow(self.main_window.current_image_index)

    def on_image_selected(self, item: QListWidgetItem) -> None:
        """Handle image selection from the list widget."""
        selected_index = self.main_window.image_list_widget.row(item)
        if selected_index != self.main_window.current_image_index:
            if not self._handle_unsaved_before_switch():
                self.main_window.image_list_widget.setCurrentRow(self.main_window.current_image_index)
                return
            self.main_window.current_image_index = selected_index
            self.main_window.load_current_image()

    def show_save_dialog(self) -> int:
        """Show a dialog asking user whether to save changes.

        Returns:
            The user's choice (Yes, No, or Cancel).

        """
        return QMessageBox.question(
            self.main_window,
            "Save Changes",
            "Do you want to save the changes to the current image?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
