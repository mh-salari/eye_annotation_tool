"""Image navigation: prev/next, list selection, unsaved-changes prompt."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QListWidgetItem, QMessageBox, QWidget

from ..state import ProjectStore
from .annotation_controller import AnnotationController

if TYPE_CHECKING:
    from PyQt5.QtWidgets import QListWidget


class NavigationController:
    """Move between images and gate the switch on unsaved annotation changes.

    Holds a reference to ``MainWindow`` only for the two things that
    are intrinsic UI concerns: the ``image_list_widget`` row
    highlighting and the ``load_current_image`` slot. Everything else
    (autosave flag, image list, modified flag, save action) goes
    through the relevant store or controller.
    """

    def __init__(
        self,
        annotation_controller: AnnotationController,
        project_store: ProjectStore,
        image_list_widget: "QListWidget",
        load_current_image: Callable[[], None],
        *,
        current_index_getter: Callable[[], int],
        current_index_setter: Callable[[int], None],
        is_modified_fn: Callable[[], bool],
        dialog_parent: QWidget,
    ) -> None:
        """Wire dependencies and the navigation-state hooks.

        The current-image index lives on MainWindow; the controller
        reads and writes it through the supplied getter/setter so it
        does not need a back-reference to the main window for that
        state.
        """
        self.annotation_controller = annotation_controller
        self.project_store = project_store
        self.image_list_widget = image_list_widget
        self.load_current_image = load_current_image
        self._current_index_getter = current_index_getter
        self._current_index_setter = current_index_setter
        self._is_modified_fn = is_modified_fn
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
        if not self._is_modified_fn():
            return True
        reply = self.show_save_dialog()
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Yes:
            self.annotation_controller.save_current_annotations()
        return True

    def next_image(self) -> None:
        """Navigate to the next image in the list."""
        index = self._current_index_getter()
        image_paths = self.project_store.image_paths()
        if index < len(image_paths) - 1:
            if not self._handle_unsaved_before_switch():
                return
            new_index = index + 1
            self._current_index_setter(new_index)
            self.load_current_image()
            self.image_list_widget.setCurrentRow(new_index)

    def prev_image(self) -> None:
        """Navigate to the previous image in the list."""
        index = self._current_index_getter()
        if index > 0:
            if not self._handle_unsaved_before_switch():
                return
            new_index = index - 1
            self._current_index_setter(new_index)
            self.load_current_image()
            self.image_list_widget.setCurrentRow(new_index)

    def on_image_selected(self, item: QListWidgetItem) -> None:
        """Handle image selection from the list widget."""
        selected_index = self.image_list_widget.row(item)
        if selected_index != self._current_index_getter():
            if not self._handle_unsaved_before_switch():
                self.image_list_widget.setCurrentRow(self._current_index_getter())
                return
            self._current_index_setter(selected_index)
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
