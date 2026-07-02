"""Image navigation: prev/next, tree selection, unsaved-changes prompt."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QMessageBox, QWidget

from ..state import ProjectStore, SessionState
from .annotation_controller import AnnotationController

if TYPE_CHECKING:
    from ..gui.image_tree import ImageTree


class NavigationController:
    """Move between images and gate the switch on unsaved annotation changes.

    Reads navigation state from :class:`SessionState` (current image
    index + dirty flag) and project state from :class:`ProjectStore`.
    The current-image index points into the project's stored image order;
    prev/next instead follow the tree's top-to-bottom display order so the
    user moves through images the way they see them.
    """

    def __init__(
        self,
        annotation_controller: AnnotationController,
        project_store: ProjectStore,
        session: SessionState,
        image_tree: "ImageTree",
        load_current_image: Callable[[], None],
        dialog_parent: QWidget,
    ) -> None:
        """Wire dependencies."""
        self.annotation_controller = annotation_controller
        self.project_store = project_store
        self.session = session
        self.image_tree = image_tree
        self.load_current_image = load_current_image
        self._dialog_parent = dialog_parent

    def handle_unsaved_before_switch(self) -> bool:
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

    def _current_path(self) -> str | None:
        """Return the active image's path, or ``None`` when nothing is loaded."""
        paths = self.project_store.image_paths()
        index = self.session.current_image_index
        return paths[index] if 0 <= index < len(paths) else None

    def _switch_to(self, path: str) -> None:
        """Make ``path`` the current image: update the index, load it, sync the tree."""
        self.session.current_image_index = self.project_store.image_paths().index(path)
        self.load_current_image()
        self.image_tree.select_path(path)

    def _navigate_by(self, delta: int) -> None:
        """Move ``delta`` images along the tree's display order, bounds-checked."""
        ordered = self.image_tree.ordered_paths()
        current = self._current_path()
        if current is None or current not in ordered:
            return
        target = ordered.index(current) + delta
        if 0 <= target < len(ordered) and self.handle_unsaved_before_switch():
            self._switch_to(ordered[target])

    def next_image(self) -> None:
        """Navigate to the next image in the tree's display order."""
        self._navigate_by(1)

    def prev_image(self) -> None:
        """Navigate to the previous image in the tree's display order."""
        self._navigate_by(-1)

    def on_image_selected(self, path: str) -> None:
        """Handle an image leaf clicked in the tree."""
        paths = self.project_store.image_paths()
        if path not in paths:
            return
        index = paths.index(path)
        if index == self.session.current_image_index:
            return
        if not self.handle_unsaved_before_switch():
            # Revert the tree's highlight to the image that's actually loaded.
            current = self._current_path()
            if current is not None:
                self.image_tree.select_path(current)
            return
        self.session.current_image_index = index
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
