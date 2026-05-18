"""Image navigation: prev/next, list selection, unsaved-changes prompt."""

from collections.abc import Callable

from PyQt5.QtWidgets import QListWidget, QListWidgetItem, QMessageBox, QWidget

from ..state import ProjectStore, SessionState
from .annotation_controller import AnnotationController


class NavigationController:
    """Move between images and gate the switch on unsaved annotation changes.

    Reads navigation state from :class:`SessionState` (current image
    index + dirty flag) and project state from :class:`ProjectStore`.
    Holds direct references to ``image_list_widget`` and the
    ``load_current_image`` slot since those are intrinsic UI concerns
    on MainWindow.
    """

    def __init__(
        self,
        annotation_controller: AnnotationController,
        project_store: ProjectStore,
        session: SessionState,
        image_list_widget: QListWidget,
        load_current_image: Callable[[], None],
        dialog_parent: QWidget,
    ) -> None:
        """Wire dependencies."""
        self.annotation_controller = annotation_controller
        self.project_store = project_store
        self.session = session
        self.image_list_widget = image_list_widget
        self.load_current_image = load_current_image
        self._dialog_parent = dialog_parent

    def _handle_unsaved_before_switch(self) -> bool:
        """Save / prompt / cancel based on autosave; return True to proceed with the switch.

        When autosave is enabled, every navigation persists the current image
        regardless of whether the user touched it — auto-detector results are
        produced automatically on image load, and skipping the save would
        leave them out of the on-disk annotation.
        """
        if self.project_store.autosave:
            self.annotation_controller.save_current_annotations(silent=True)
            return True
        if not self.session.modified:
            return True
        reply = self.show_save_dialog()
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Yes:
            self.annotation_controller.save_current_annotations()
        return True

    def next_image(self) -> None:
        """Navigate to the next image in the list."""
        index = self.session.current_image_index
        if index < len(self.project_store.image_paths()) - 1:
            if not self._handle_unsaved_before_switch():
                return
            self.session.current_image_index = index + 1
            self.load_current_image()
            self.image_list_widget.setCurrentRow(self.session.current_image_index)

    def prev_image(self) -> None:
        """Navigate to the previous image in the list."""
        index = self.session.current_image_index
        if index > 0:
            if not self._handle_unsaved_before_switch():
                return
            self.session.current_image_index = index - 1
            self.load_current_image()
            self.image_list_widget.setCurrentRow(self.session.current_image_index)

    def on_image_selected(self, item: QListWidgetItem) -> None:
        """Handle image selection from the list widget."""
        selected_index = self.image_list_widget.row(item)
        if selected_index != self.session.current_image_index:
            if not self._handle_unsaved_before_switch():
                self.image_list_widget.setCurrentRow(self.session.current_image_index)
                return
            self.session.current_image_index = selected_index
            self.load_current_image()

    def show_save_dialog(self) -> int:
        """Show a dialog asking user whether to save changes.

        Returns:
            The user's choice (Yes, No, or Cancel).

        """
        return QMessageBox.question(
            self._dialog_parent,
            "Save Changes",
            "Do you want to save the changes to the current image?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
