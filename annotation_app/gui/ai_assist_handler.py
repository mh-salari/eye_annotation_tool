from itertools import starmap

from PyQt5.QtCore import QPointF, QSizeF
from PyQt5.QtWidgets import QMessageBox


class AIAssistHandler:
    def __init__(self, main_window):
        self.main_window = main_window

    def on_ai_assist_requested(self):
        if self.main_window.current_image_index >= 0:
            image_path = self.main_window.image_paths[self.main_window.current_image_index]

            # Save the current state before making changes
            self.main_window.image_viewer.save_state()

            changes_made = self.detect_and_update("pupil_detector", image_path)
            changes_made |= self.detect_and_update("iris_detector", image_path)
            changes_made |= self.detect_and_update("eyelid_detector", image_path)

            if changes_made:
                # Save the new state after making changes
                self.main_window.image_viewer.save_state()
                self.main_window.set_annotation_modified(True)
                self.main_window.image_viewer.update_image()
                self.update_annotation_controls()
                self.main_window.image_viewer.annotation_changed.emit()

    def detect_and_update(self, detector_type, image_path):
        changes_made = False
        detector_name = self.main_window.settings_handler.get_setting(detector_type)
        if detector_name != "disabled":
            if detector_type == "pupil_detector":
                detector = self.main_window.plugin_manager.get_pupil_detector(detector_name)
            elif detector_type == "iris_detector":
                detector = self.main_window.plugin_manager.get_iris_detector(detector_name)
            else:  # eyelid_detector
                detector = self.main_window.plugin_manager.get_eyelid_detector(detector_name)

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
                            self.main_window.image_viewer.iris_ellipse = (
                                center,
                                size,
                                angle,
                            )
                            self.main_window.image_viewer.iris_points = list(starmap(QPointF, points))
                    changes_made = True
                except Exception as e:
                    QMessageBox.warning(
                        self.main_window,
                        f"{detector_type.split('_')[0].capitalize()} Detection Error",
                        f"Error detecting {detector_type.split('_')[0]}: {e!s}",
                    )
        else:
            if detector_type == "pupil_detector":
                self.main_window.image_viewer.pupil_ellipse = None
                self.main_window.image_viewer.pupil_points = []
            elif detector_type == "iris_detector":
                self.main_window.image_viewer.iris_ellipse = None
                self.main_window.image_viewer.iris_points = []
            else:  # eyelid_detector
                self.main_window.image_viewer.eyelid_contour_points = []
                self.main_window.image_viewer.fitted_eyelid_curve = None
            changes_made = True
        return changes_made

    def update_annotation_controls(self):
        pupil_detector_name = self.main_window.settings_handler.get_setting("pupil_detector")
        iris_detector_name = self.main_window.settings_handler.get_setting("iris_detector")
        eyelid_detector_name = self.main_window.settings_handler.get_setting("eyelid_detector")

        if eyelid_detector_name != "disabled":
            self.main_window.annotation_controls.set_current_annotation("eyelid_contour")
        elif iris_detector_name != "disabled":
            self.main_window.annotation_controls.set_current_annotation("iris")
        elif pupil_detector_name != "disabled":
            self.main_window.annotation_controls.set_current_annotation("pupil")
