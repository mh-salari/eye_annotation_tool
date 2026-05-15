"""Handler for auto-detector annotation functionality."""

from itertools import starmap
from typing import TYPE_CHECKING

from PyQt5.QtCore import QPointF, QSizeF
from PyQt5.QtWidgets import QMessageBox

from annotation_app.auto_detectors.plugins.glint_detectors.threshold_glint_detector import ThresholdGlintDetector
from annotation_app.auto_detectors.plugins.pupil_detectors.threshold_pupil_detector import ThresholdPupilDetector

if TYPE_CHECKING:
    from .main_window import MainWindow


class AutoDetectorsHandler:
    """Runs the active auto-detector plugins against the current image."""

    def __init__(self, main_window: "MainWindow") -> None:
        """Initialize the AutoDetectorsHandler."""
        self.main_window = main_window

    def on_auto_detector_requested(self) -> None:
        """Handle the Auto Detect button click to run detectors on the current image."""
        if self.main_window.current_image_index >= 0:
            image_path = self.main_window.image_paths[self.main_window.current_image_index]

            # Save the current state before making changes
            self.main_window.image_viewer.save_state()

            # Get current annotation type to determine which detector to run
            current_annotation = self.main_window.image_viewer.current_annotation

            changes_made = False
            if current_annotation == "pupil":
                changes_made = self.detect_and_update("pupil_detector", image_path)
            elif current_annotation == "limbus":
                changes_made = self.detect_and_update("limbus_detector", image_path)
            elif current_annotation == "eyelid_contour":
                changes_made = self.detect_and_update("eyelid_detector", image_path)
            elif current_annotation == "glint":
                changes_made = self.detect_and_update_glint(image_path)

            if changes_made:
                # Save the new state after making changes
                self.main_window.image_viewer.save_state()
                self.main_window.image_viewer.save_current_eye_data()
                self.main_window.set_annotation_modified(True)
                self.main_window.image_viewer.update_image()
                self.main_window.image_viewer.annotation_changed.emit()

    def detect_and_update(self, detector_type: str, image_path: str) -> bool:
        """Run a detector and update annotations."""
        changes_made = False
        detector_name = self.main_window.settings_handler.get_setting(detector_type)

        # Use threshold detector with ROI if available
        roi = self.main_window.image_viewer.get_roi()
        if roi and detector_type == "pupil_detector":
            # Use threshold detector
            detector = ThresholdPupilDetector(roi=roi)
        elif detector_name != "disabled":
            if detector_type == "pupil_detector":
                detector = self.main_window.plugin_manager.get_pupil_detector(detector_name)
            elif detector_type == "limbus_detector":
                detector = self.main_window.plugin_manager.get_limbus_detector(detector_name)
            else:  # eyelid_detector
                detector = self.main_window.plugin_manager.get_eyelid_detector(detector_name)
        else:
            detector = None

        if detector:
            try:
                if detector_type == "eyelid_detector":
                    points = detector.detect(image_path)
                    self.main_window.image_viewer.eyelid_contour_points = list(starmap(QPointF, points))
                    self.main_window.image_viewer.fitted_eyelid_curve = None  # Clear the fitted curve
                else:
                    ellipse, points = detector.detect(image_path)
                    center = QPointF(ellipse["center"][0], ellipse["center"][1])
                    size = QSizeF(ellipse["axes"][0], ellipse["axes"][1])
                    angle = ellipse["angle"]
                    if detector_type == "pupil_detector":
                        self.main_window.image_viewer.pupil_ellipse = (
                            center,
                            size,
                            angle,
                        )
                        self.main_window.image_viewer.pupil_points = list(starmap(QPointF, points))
                    else:
                        self.main_window.image_viewer.limbus_ellipse = (
                            center,
                            size,
                            angle,
                        )
                        self.main_window.image_viewer.limbus_points = list(starmap(QPointF, points))
                changes_made = True
            except Exception as e:
                QMessageBox.warning(
                    self.main_window,
                    f"{detector_type.split('_', maxsplit=1)[0].capitalize()} Detection Error",
                    f"Error detecting {detector_type.split('_', maxsplit=1)[0]}: {e!s}",
                )
        else:
            if detector_type == "pupil_detector":
                self.main_window.image_viewer.pupil_ellipse = None
                self.main_window.image_viewer.pupil_points = []
            elif detector_type == "limbus_detector":
                self.main_window.image_viewer.limbus_ellipse = None
                self.main_window.image_viewer.limbus_points = []
            else:  # eyelid_detector
                self.main_window.image_viewer.eyelid_contour_points = []
                self.main_window.image_viewer.fitted_eyelid_curve = None
            changes_made = True
        return changes_made

    def detect_and_update_glint(self, image_path: str) -> bool:
        """Run glint detector and update glint annotations."""
        changes_made = False

        # Use threshold detector with ROI if available
        roi = self.main_window.image_viewer.get_roi()
        if roi:
            # Use threshold glint detector
            detector = ThresholdGlintDetector(roi=roi)

            try:
                points = detector.detect(image_path)
                self.main_window.image_viewer.glint_points = list(starmap(QPointF, points))
                changes_made = True
            except Exception as e:
                QMessageBox.warning(
                    self.main_window,
                    "Glint Detection Error",
                    f"Error detecting glints: {e!s}",
                )
        else:
            QMessageBox.information(
                self.main_window,
                "ROI Required",
                "Please draw an ROI first before using glint auto-detection.",
            )

        return changes_made

    def update_annotation_controls(self) -> None:
        """Update annotation controls based on active detectors."""
        pupil_detector_name = self.main_window.settings_handler.get_setting("pupil_detector")
        limbus_detector_name = self.main_window.settings_handler.get_setting("limbus_detector")
        eyelid_detector_name = self.main_window.settings_handler.get_setting("eyelid_detector")

        if eyelid_detector_name != "disabled":
            self.main_window.annotation_controls.set_current_annotation("eyelid_contour")
        elif limbus_detector_name != "disabled":
            self.main_window.annotation_controls.set_current_annotation("limbus")
        elif pupil_detector_name != "disabled":
            self.main_window.annotation_controls.set_current_annotation("pupil")
