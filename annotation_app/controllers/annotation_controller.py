import os
from PyQt5.QtWidgets import QMessageBox
from ..utils.annotation_io import (
    save_annotations,
    load_annotations,
    get_annotation_path,
)


class AnnotationController:
    def __init__(self, main_window):
        self.main_window = main_window

    def save_annotations(self):
        self.save_current_annotations()

    def save_current_annotations(self):
        if (
            self.main_window.annotation_modified
            and 0
            <= self.main_window.current_image_index
            < len(self.main_window.image_paths)
        ):
            image_path = self.main_window.image_paths[
                self.main_window.current_image_index
            ]
            annotation_path = get_annotation_path(image_path)

            if os.path.exists(annotation_path):
                reply = QMessageBox.question(
                    self.main_window,
                    "Update Annotations",
                    "An annotation file already exists. Do you want to update it?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    return

            annotation_data = self.main_window.image_viewer.get_annotation_data()
            save_annotations(annotation_path, **annotation_data)
            self.main_window.set_annotation_modified(False)
            # QMessageBox.information(self.main_window, "Success", "Annotations saved successfully.")

    def load_annotations(self):
        if (
            0
            <= self.main_window.current_image_index
            < len(self.main_window.image_paths)
        ):
            image_path = self.main_window.image_paths[
                self.main_window.current_image_index
            ]
            annotation_path = get_annotation_path(image_path)
            annotation_data = load_annotations(annotation_path)
            self.main_window.image_viewer.set_annotation_data(annotation_data)
            self.main_window.set_annotation_modified(False)

    def check_unsaved_changes(self):
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
            elif reply == QMessageBox.Discard:
                return True
            else:  # Cancel
                return False
        return True
