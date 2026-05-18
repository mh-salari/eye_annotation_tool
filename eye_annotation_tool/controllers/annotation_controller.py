"""Save and load per-image annotations."""

from pathlib import Path

from PyQt5.QtWidgets import QMessageBox, QWidget

from ..gui.image_viewer import ImageViewer
from ..state import ProjectStore, SessionState
from ..utils.annotation_io import (
    get_annotation_path,
    load_annotations,
    save_annotations,
)
from .binocular_controller import BinocularController
from .detection_controller import DetectionController


class AnnotationController:
    """Save and load the current image's annotation JSON.

    Composes the annotation payload from :class:`ImageViewer` (manual
    eye data), :class:`DetectionController` (per-image plugin blocks),
    and :class:`BinocularController` (per-image binocular flag +
    divider override), and persists through :func:`save_annotations`.
    Session-scoped state (current image index, dirty flag) lives on
    :class:`SessionState`.
    """

    def __init__(
        self,
        image_viewer: ImageViewer,
        detection_controller: DetectionController,
        binocular_controller: BinocularController,
        project_store: ProjectStore,
        session: SessionState,
        dialog_parent: QWidget,
    ) -> None:
        """Wire dependencies."""
        self.image_viewer = image_viewer
        self.detection_controller = detection_controller
        self.binocular_controller = binocular_controller
        self.project_store = project_store
        self.session = session
        self._dialog_parent = dialog_parent

    def save_annotations(self) -> None:
        """Save the current image's annotation file (interactive entry point)."""
        self.save_current_annotations()

    def save_current_annotations(self, silent: bool = False) -> None:
        """Persist annotations + detection results for the current image.

        When ``silent`` is True the overwrite-confirmation dialog is skipped
        AND the modified-flag gate is dropped — used by the autosave-on-
        image-change path so every visited image's auto-detector result
        lands on disk even when the user didn't touch the canvas.
        Interactive (non-silent) saves still no-op when nothing changed.
        """
        image_paths = self.project_store.image_paths()
        index = self.session.current_image_index
        if not (0 <= index < len(image_paths)):
            return
        if not silent and not self.session.modified:
            return
        annotation_path = get_annotation_path(image_paths[index])
        if not silent and Path(annotation_path).exists():
            reply = QMessageBox.question(
                self._dialog_parent,
                "Update Annotations",
                "An annotation file already exists. Do you want to update it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return
        save_annotations(
            annotation_path,
            self.image_viewer.get_annotation_data(),
            binocular_mode=self.binocular_controller.is_binocular,
            divider_x_norm=self.binocular_controller.divider_override_for_current_image(),
            detections=self.detection_controller.collect_detections_for_save(),
        )
        self.session.modified = False

    def load_annotations(self) -> None:
        """Load the saved annotation file for the current image (if any)."""
        image_paths = self.project_store.image_paths()
        index = self.session.current_image_index
        if not (0 <= index < len(image_paths)):
            return
        payload = load_annotations(get_annotation_path(image_paths[index]))
        self.binocular_controller.apply_loaded_image_meta(
            binocular_mode=payload["binocular_mode"],
            divider_x_norm=payload["divider_x_norm"],
        )
        self.image_viewer.set_annotation_data(payload["eye_data"])
        self.detection_controller.apply_loaded_detections(payload["detections"])
        self.session.modified = False

    def check_unsaved_changes(self) -> bool:
        """Prompt to save unsaved changes. Return True if it's safe to proceed."""
        if not self.session.modified:
            return True
        reply = QMessageBox.question(
            self._dialog_parent,
            "Unsaved Changes",
            "You have unsaved changes. Do you want to save before exiting?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if reply == QMessageBox.Save:
            self.save_annotations()
            return True
        return reply == QMessageBox.Discard
