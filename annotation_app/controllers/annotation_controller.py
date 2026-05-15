"""Controller for managing annotation save and load operations."""

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QMessageBox

from ..utils.annotation_io import (
    get_annotation_path,
    load_annotations,
    save_annotations,
)

if TYPE_CHECKING:
    from ..gui.main_window import MainWindow


class AnnotationController:
    """Handles saving, loading, and validation of annotation data."""

    def __init__(self, main_window: "MainWindow") -> None:
        """Initialize the AnnotationController.

        Args:
            main_window: Reference to the main application window.

        """
        self.main_window = main_window

    def save_annotations(self) -> None:
        """Save annotations for the current image."""
        self.save_current_annotations()

    def save_current_annotations(self, silent: bool = False) -> None:
        """Save current image annotations to file.

        When ``silent`` is True the overwrite-confirmation dialog is skipped —
        used by the autosave-on-image-change path so navigation isn't blocked.
        """
        if self.main_window.annotation_modified and 0 <= self.main_window.current_image_index < len(
            self.main_window.image_paths
        ):
            image_path = self.main_window.image_paths[self.main_window.current_image_index]
            annotation_path = get_annotation_path(image_path)

            if not silent and Path(annotation_path).exists():
                reply = QMessageBox.question(
                    self.main_window,
                    "Update Annotations",
                    "An annotation file already exists. Do you want to update it?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    return

            eye_data = self.main_window.image_viewer.get_annotation_data()
            tuning = self.main_window.get_current_tuning()
            save_annotations(
                annotation_path,
                eye_data,
                single_eye_mode=self.main_window.single_eye_mode,
                tuning=tuning,
            )
            self.main_window.set_annotation_modified(False)

    def load_annotations(self) -> None:
        """Load annotations for the current image from file."""
        if 0 <= self.main_window.current_image_index < len(self.main_window.image_paths):
            image_path = self.main_window.image_paths[self.main_window.current_image_index]
            annotation_path = get_annotation_path(image_path)
            eye_data, tuning = load_annotations(annotation_path)
            self.main_window.image_viewer.set_annotation_data(eye_data)
            self.main_window.apply_tuning(tuning)
            self.main_window.set_annotation_modified(False)

    def check_unsaved_changes(self) -> bool:
        """Check for unsaved changes and prompt user to save.

        Returns:
            True if it's safe to proceed, False if user cancelled.

        """
        if self.main_window.annotation_modified:
            reply = QMessageBox.question(
                self.main_window,
                "Unsaved Changes",
                "You have unsaved changes. Do you want to save before exiting?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )

            if reply == QMessageBox.Save:
                self.save_annotations()
                return True
            # Cancel or Discard
            return reply == QMessageBox.Discard
        return True
