"""Save and load per-image annotations."""

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
    """Save and load the current image's annotation JSON."""

    def __init__(self, main_window: "MainWindow") -> None:
        """Initialise with a back-reference to the main window."""
        self.main_window = main_window

    def save_annotations(self) -> None:
        """Save the current image's annotation file (interactive entry point)."""
        self.save_current_annotations()

    def save_current_annotations(self, silent: bool = False) -> None:
        """Persist annotations + detection results for the current image.

        When ``silent`` is True the overwrite-confirmation dialog is skipped
        AND the ``annotation_modified`` gate is dropped — used by the
        autosave-on-image-change path so every visited image's auto-detector
        result lands on disk even when the user didn't touch the canvas.
        Interactive (non-silent) saves still no-op when nothing changed.
        """
        if not (0 <= self.main_window.current_image_index < len(self.main_window.image_paths)):
            return
        if not silent and not self.main_window.annotation_modified:
            return
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
        detections = self.main_window.detection_controller.collect_detections_for_save()
        save_annotations(
            annotation_path,
            eye_data,
            binocular_mode=self.main_window.binocular_controller.is_binocular,
            divider_x_norm=self.main_window.binocular_controller.divider_override_for_current_image(),
            detections=detections,
        )
        self.main_window.set_annotation_modified(False)

    def load_annotations(self) -> None:
        """Load the saved annotation file for the current image (if any)."""
        if not (0 <= self.main_window.current_image_index < len(self.main_window.image_paths)):
            return
        image_path = self.main_window.image_paths[self.main_window.current_image_index]
        annotation_path = get_annotation_path(image_path)
        payload = load_annotations(annotation_path)
        self.main_window.binocular_controller.apply_loaded_image_meta(
            binocular_mode=payload["binocular_mode"],
            divider_x_norm=payload["divider_x_norm"],
        )
        self.main_window.image_viewer.set_annotation_data(payload["eye_data"])
        self.main_window.detection_controller.apply_loaded_detections(payload["detections"])
        self.main_window.set_annotation_modified(False)

    def check_unsaved_changes(self) -> bool:
        """Prompt to save unsaved changes. Return True if it's safe to proceed."""
        if not self.main_window.annotation_modified:
            return True
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
        return reply == QMessageBox.Discard
