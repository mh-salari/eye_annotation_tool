"""Main application window for the eye annotation tool."""

import ast
from pathlib import Path

from PyQt5.QtCore import QEvent, QRect, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QCloseEvent, QIcon, QPixmap, QScreen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..auto_detectors import PluginManager
from ..controllers.annotation_controller import AnnotationController
from ..controllers.navigation_controller import NavigationController
from ..utils.project_settings import PROJECT_DETECTOR_KEYS, load_project_settings, save_project_settings
from ..utils.settings_handler import SettingsHandler
from .annotation_controls import MODE_ANNOTATE, MODE_MANUAL_THRESHOLD, AnnotationControlPanel
from .auto_detectors_handler import AutoDetectorsHandler
from .custom_widgets import MaterialButton
from .image_viewer import ImageViewer
from .manual_threshold_worker import DetectionWorker
from .menu_handler import MenuHandler
from .shortcut_handler import ShortcutHandler

# Slider drags fire one params_changed per pixel. Collapse the resulting burst
# to a single detection ~100 ms after the last change so the worker isn't
# flooded with stale intermediates.
MANUAL_THRESHOLD_DEBOUNCE_MS = 100


class MainWindow(QMainWindow):
    """Main application window containing all UI components and controllers."""

    # Queued-connection bridge into the off-thread DetectionWorker. Emitted
    # after the debounce timer fires; carries the grayscale image and the
    # latest parameter dict from ManualThresholdPanel.
    manual_threshold_detect_requested = pyqtSignal(object, object)

    def __init__(self, cli_single_eye: bool = False) -> None:
        """Initialize the MainWindow.

        Args:
            cli_single_eye: When True, force single-eye mode on at startup
                regardless of any per-project setting.

        """
        super().__init__()
        self.setWindowTitle("EyE Annotation Tool")
        self.settings_handler = SettingsHandler()
        self.plugin_manager = PluginManager()
        self._cli_single_eye = bool(cli_single_eye)
        self.single_eye_mode = self._cli_single_eye
        self.autosave_enabled = False
        self.project_dir: str | None = None

        self.setup_ui()
        self.setup_variables()

        self.annotation_controller = AnnotationController(self)
        self.navigation_controller = NavigationController(self)
        self.menu_handler = MenuHandler(self)
        self.shortcut_handler = ShortcutHandler(self)
        self.auto_detectors_handler = AutoDetectorsHandler(self)

        # Manual Threshold mode runs detect_pupil_and_glints on a background
        # thread so the GUI stays responsive while the user drags sliders.
        # The worker lives for the whole MainWindow lifetime; we feed it
        # (image, params) tuples and it emits results back.
        self._mt_thread = QThread(self)
        self._mt_worker = DetectionWorker()
        self._mt_worker.moveToThread(self._mt_thread)
        self._mt_thread.start()
        self._mt_debounce = QTimer(self)
        self._mt_debounce.setSingleShot(True)
        self._mt_debounce.setInterval(MANUAL_THRESHOLD_DEBOUNCE_MS)
        self._mt_pending_params: dict | None = None
        # Set true by apply_tuning so the next detection_ready (which is the
        # re-run after a saved load) doesn't mark the annotation as modified.
        self._mt_skip_next_modified_mark = False

        self.menu_handler.setup_menu()
        self.shortcut_handler.setup_shortcuts()
        self.connect_signals()

        # Set the application icon
        icon_path = str(Path(__file__).parent / ".." / "resources" / "app_icon.ico")
        self.setWindowIcon(QIcon(icon_path))

        # Store the screen size for later use
        self.screen = QApplication.primaryScreen().availableGeometry()

        # Set the window to maximized state
        self.showMaximized()

        # Install event filter to catch window state changes
        self.installEventFilter(self)

    def setup_ui(self) -> None:
        """Set up the user interface components."""
        central_widget = QWidget()
        main_layout = QHBoxLayout()

        # Left panel
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        self.load_images_button = MaterialButton("Load Images")
        self.load_folder_button = MaterialButton("Load Folder")
        self.prev_image_button = MaterialButton("Previous Image")
        self.next_image_button = MaterialButton("Next Image")
        self.save_annotations_button = MaterialButton("Save Annotations")
        self.autosave_checkbox = QCheckBox("Autosave on image change")
        self.autosave_checkbox.toggled.connect(self._on_autosave_changed)

        left_layout.addWidget(self.load_images_button)
        left_layout.addWidget(self.load_folder_button)
        left_layout.addWidget(self.prev_image_button)
        left_layout.addWidget(self.next_image_button)
        left_layout.addWidget(self.save_annotations_button)
        left_layout.addWidget(self.autosave_checkbox)

        self.image_list_widget = QListWidget()
        left_layout.addWidget(QLabel("Loaded Images:"))
        left_layout.addWidget(self.image_list_widget)

        left_layout.addStretch(1)
        left_panel.setLayout(left_layout)

        # Central area for image viewer
        self.image_viewer = ImageViewer()

        # Right panel for annotation controls. Wrapped in a QScrollArea so
        # taller Annotate-mode content can't push the window past the screen.
        self.annotation_controls = AnnotationControlPanel()
        right_scroll = QScrollArea()
        right_scroll.setWidget(self.annotation_controls)
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setFixedWidth(360)  # 340 panel + room for the vertical scrollbar
        right_scroll.setFrameShape(QScrollArea.NoFrame)

        main_layout.addWidget(left_panel)
        main_layout.addWidget(self.image_viewer, 1)
        main_layout.addWidget(right_scroll)

        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        # Status bar surfaces non-blocking messages such as Manual Threshold
        # detection failures (no dark contour, no glints, etc.).
        self.setStatusBar(QStatusBar())

        # Set focus to the image viewer
        self.image_viewer.setFocus()

    def setup_variables(self) -> None:
        """Initialize instance variables."""
        self.image_paths = []
        self.current_image_index = -1
        self.annotation_modified = False

    def set_annotation_modified(self, modified: bool) -> None:
        """Set the annotation modified flag."""
        self.annotation_modified = modified

    def connect_signals(self) -> None:
        """Connect signals and slots for UI components."""
        self.load_images_button.clicked.connect(self.load_images)
        self.load_folder_button.clicked.connect(self.load_folder)
        self.prev_image_button.clicked.connect(self.navigation_controller.prev_image)
        self.next_image_button.clicked.connect(self.navigation_controller.next_image)
        self.save_annotations_button.clicked.connect(self.annotation_controller.save_annotations)
        self.image_list_widget.itemClicked.connect(self.navigation_controller.on_image_selected)

        self.annotation_controls.annotation_changed.connect(self.image_viewer.set_current_annotation)
        self.annotation_controls.eye_changed.connect(self._on_eye_changed)
        self.annotation_controls.fit_annotation_requested.connect(self.image_viewer.fit_annotation)
        self.annotation_controls.clear_selected_annotation_requested.connect(self.image_viewer.clear_selected_ellipse)
        self.annotation_controls.clear_pupil_requested.connect(self.image_viewer.clear_pupil_points)
        self.annotation_controls.clear_iris_requested.connect(self.image_viewer.clear_iris_points)
        self.annotation_controls.clear_eyelid_points_requested.connect(self.image_viewer.clear_eyelid_points)
        self.annotation_controls.clear_glint_points_requested.connect(self.image_viewer.clear_glint_points)
        self.annotation_controls.clear_all_requested.connect(self.image_viewer.clear_all)
        self.annotation_controls.auto_detector_requested.connect(
            self.auto_detectors_handler.on_auto_detector_requested
        )
        self.annotation_controls.roi_toggle_requested.connect(self.image_viewer.toggle_roi_mode)
        self.annotation_controls.roi_clear_requested.connect(self.image_viewer.clear_roi)

        self.image_viewer.annotation_changed.connect(self.on_annotation_changed)
        self.image_viewer.annotation_type_changed.connect(self.annotation_controls.set_current_annotation)

        # Manual Threshold live-detection signal graph:
        #   slider drag -> ManualThresholdPanel.params_changed
        #     -> debounce timer
        #       -> manual_threshold_detect_requested  (queued, cross-thread)
        #         -> DetectionWorker.detect
        #           -> detection_ready / detection_failed
        #             -> ImageViewer.set_manual_threshold_detection / status bar
        # Top-of-panel mode switcher (Annotate / Manual Threshold) drives the
        # detector on/off via annotation_controls.mode_changed.
        panel = self.annotation_controls.manual_threshold_panel
        panel.params_changed.connect(self._on_manual_threshold_params_changed)
        self.annotation_controls.mode_changed.connect(self._on_mode_changed)
        self._mt_debounce.timeout.connect(self._on_manual_threshold_debounce_fired)
        self.manual_threshold_detect_requested.connect(
            self._mt_worker.detect,
            Qt.QueuedConnection,
        )
        self._mt_worker.detection_ready.connect(self._on_manual_threshold_detection_ready)
        self._mt_worker.detection_failed.connect(self._on_manual_threshold_detection_failed)
        self.image_viewer.image_loaded.connect(self._on_image_loaded_for_manual_threshold)

        # Pupil/Glint ROI flow: panel toggle -> viewer drag-edit mode; viewer
        # roi changes -> re-trigger detection with the new ROI.
        panel.pupil_roi_mode_changed.connect(self._on_pupil_roi_mode_changed)
        panel.glint_roi_mode_changed.connect(self._on_glint_roi_mode_changed)
        panel.clear_pupil_roi_requested.connect(self._on_clear_pupil_roi)
        panel.clear_glint_roi_requested.connect(self._on_clear_glint_roi)
        self.image_viewer.pupil_roi_changed.connect(self._on_mt_roi_changed)
        self.image_viewer.glint_roi_changed.connect(self._on_mt_roi_changed)

    IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp")

    def load_images(self) -> None:
        """Open file dialog to load image files."""
        file_dialog = QFileDialog()
        image_files, _ = file_dialog.getOpenFileNames(
            self, "Select Image Files", "", "Image Files (*.png *.jpg *.bmp)"
        )
        if image_files:
            self.image_paths = image_files
            self.current_image_index = 0
            self._load_project_settings_from(image_files[0])
            self.update_image_list()
            self.load_current_image()

    def load_folder(self) -> None:
        """Pick a folder via dialog and load every supported image recursively."""
        folder = QFileDialog.getExistingDirectory(self, "Select Image Folder", "")
        if folder:
            self.load_folder_path(folder)

    def load_folder_path(self, folder: str) -> None:
        """Recursively load every supported image under ``folder``.

        Used by both the Load Folder dialog and the ``--folder`` CLI flag.
        The chosen folder is the project root (project settings live here),
        not whichever subdir an image happens to be in.
        """
        folder_path = Path(folder)
        image_paths = sorted(
            str(p) for p in folder_path.rglob("*") if p.is_file() and p.suffix.lower() in self.IMAGE_SUFFIXES
        )
        if not image_paths:
            QMessageBox.warning(
                self,
                "No Images Found",
                f"No image files found under {folder_path}.",
            )
            return
        self.image_paths = image_paths
        self.current_image_index = 0
        self._apply_project_settings(str(folder_path))
        self.update_image_list()
        self.load_current_image()

    def _load_project_settings_from(self, image_path: str) -> None:
        """Read the project settings file in the image's folder and apply it."""
        self._apply_project_settings(str(Path(image_path).parent))

    def _apply_project_settings(self, project_dir: str) -> None:
        """Set the active project directory and apply every value in its file.

        Applies both ``single_eye_mode`` (CLI ``--single-eye`` still wins so
        an explicit invocation override is never silently downgraded) and
        the per-project auto-detector choices. Detector overrides are pushed
        into ``settings_handler`` so the rest of the app sees them, and the
        Auto Detectors menu is refreshed.
        """
        self.project_dir = project_dir
        project_settings = load_project_settings(project_dir)
        effective_single_eye = self._cli_single_eye or bool(project_settings.get("single_eye_mode", False))
        self._apply_single_eye_mode(effective_single_eye)
        detectors_changed = False
        for key in PROJECT_DETECTOR_KEYS:
            if key in project_settings:
                self.settings_handler.set_setting(key, project_settings[key])
                detectors_changed = True
        if detectors_changed:
            self.menu_handler.update_menu_checks()
        # Restore the last used mode for this project; setChecked fires the
        # button's toggled signal which drives the full _on_mode_changed flow.
        saved_mode = project_settings.get("current_mode", MODE_ANNOTATE)
        if saved_mode == MODE_MANUAL_THRESHOLD:
            self.annotation_controls.mode_manual_threshold_button.setChecked(True)
        else:
            self.annotation_controls.mode_annotate_button.setChecked(True)
        # Restore autosave toggle from the project file.
        autosave = bool(project_settings.get("autosave", False))
        self.autosave_enabled = autosave
        self.autosave_checkbox.blockSignals(True)
        self.autosave_checkbox.setChecked(autosave)
        self.autosave_checkbox.blockSignals(False)

    def _apply_single_eye_mode(self, enabled: bool) -> None:
        """Propagate the single-eye flag to the dependent widgets."""
        self.single_eye_mode = enabled
        self.image_viewer.set_single_eye_mode(enabled)
        self.annotation_controls.set_single_eye_mode(enabled)

    def _on_eye_changed(self, eye: str) -> None:
        """Translate the eye radio into single-eye mode and viewer state."""
        if eye == "single":
            self._apply_single_eye_mode(True)
        else:
            if self.single_eye_mode:
                self._apply_single_eye_mode(False)
            self.image_viewer.switch_eye(eye)
        if self.project_dir is not None:
            settings = load_project_settings(self.project_dir)
            settings["single_eye_mode"] = self.single_eye_mode
            save_project_settings(self.project_dir, settings)

    def update_image_list(self) -> None:
        """Update the image list widget with current image paths.

        When a project folder is set, display each entry as its path relative
        to that root so files with the same basename in different subdirs are
        distinguishable (e.g. ``cal_dark/left_eye_target_0.png`` vs
        ``cal_bright/left_eye_target_0.png``).
        """
        self.image_list_widget.clear()
        project_root = Path(self.project_dir) if self.project_dir else None
        for image_path in self.image_paths:
            p = Path(image_path)
            if project_root is not None:
                try:
                    label = str(p.relative_to(project_root))
                except ValueError:
                    label = p.name
            else:
                label = p.name
            self.image_list_widget.addItem(label)
        if self.current_image_index >= 0:
            self.image_list_widget.setCurrentRow(self.current_image_index)

    def load_current_image(self) -> None:
        """Load and display the current image with its annotations."""
        if 0 <= self.current_image_index < len(self.image_paths):
            image_path = self.image_paths[self.current_image_index]
            if self.image_viewer.load_image(image_path):
                self.setWindowTitle(f"EyE Annotation Tool - {Path(image_path).name}")
                self.annotation_controller.load_annotations()
            else:
                QMessageBox.critical(self, "Error", f"Failed to load image: {image_path}")

    def save_current_annotations(self) -> None:
        """Save annotations for the current image."""
        self.annotation_controller.save_current_annotations()

    def on_annotation_changed(self) -> None:
        """Handle annotation change event."""
        self.set_annotation_modified(True)

    def _on_autosave_changed(self, enabled: bool) -> None:
        """Persist the autosave toggle in project settings."""
        self.autosave_enabled = enabled
        if self.project_dir is not None:
            project_settings = load_project_settings(self.project_dir)
            project_settings["autosave"] = enabled
            save_project_settings(self.project_dir, project_settings)

    def get_current_tuning(self) -> dict | None:
        """Collect Manual-Threshold state for persistence; ``None`` if nothing to save."""
        viewer = self.image_viewer
        panel = self.annotation_controls.manual_threshold_panel
        has_state = (
            viewer.manual_threshold_detection is not None
            or viewer.pupil_roi is not None
            or viewer.glint_roi is not None
        )
        if not has_state:
            return None
        return {
            "thresholds": panel.current_params(),
            "pupil_roi": viewer.pupil_roi,
            "glint_roi": viewer.glint_roi,
            "detection": viewer.manual_threshold_detection,
        }

    def apply_tuning(self, tuning: dict | None) -> None:
        """Restore Manual-Threshold state after loading an annotation file.

        When we restored a tuning from disk, set ``_mt_skip_next_modified_mark``
        so the worker's re-run (triggered by ``image_loaded``) doesn't flip
        the modified flag back on. With no tuning on disk, the next detection
        is genuinely unsaved state and we *do* want the modified flag set.
        """
        viewer = self.image_viewer
        panel = self.annotation_controls.manual_threshold_panel
        if tuning is None:
            viewer.set_pupil_roi(None)
            viewer.set_glint_roi(None)
            viewer.set_manual_threshold_detection(None)
            self._mt_skip_next_modified_mark = False
            return
        thresholds = tuning.get("thresholds")
        if thresholds:
            panel.set_params(thresholds)
        viewer.set_pupil_roi(tuning.get("pupil_roi"))
        viewer.set_glint_roi(tuning.get("glint_roi"))
        viewer.set_manual_threshold_detection(tuning.get("detection"))
        self._mt_skip_next_modified_mark = True

    # ----- Manual Threshold mode -----

    def _on_manual_threshold_params_changed(self, params: dict) -> None:
        """Buffer the new parameters and (re)start the debounce timer.

        Sliders fire one ``params_changed`` per pixel of drag. We collapse
        the burst by storing only the most recent payload and resetting the
        single-shot timer, so a steady drag results in one detection at the
        end of the drag rather than dozens.
        """
        self._mt_pending_params = params
        self._mt_debounce.start()
        self.set_annotation_modified(True)

    def _on_manual_threshold_debounce_fired(self) -> None:
        """Dispatch the buffered detection to the worker.

        Skipped silently if no image / grayscale / params are available.
        ROIs are pulled from the image viewer at dispatch time so they're
        always in sync with the drawn rectangles.
        """
        if self._mt_pending_params is None:
            return
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return
        params = dict(self._mt_pending_params)
        params["pupil_roi"] = self.image_viewer.pupil_roi
        params["glint_roi"] = self.image_viewer.glint_roi
        self.manual_threshold_detect_requested.emit(image, params)

    def _on_mode_changed(self, mode: str) -> None:
        """React to the Annotate / Manual Threshold mode switcher."""
        panel = self.annotation_controls.manual_threshold_panel
        eye_selector = self.annotation_controls.eye_selector
        if mode == MODE_MANUAL_THRESHOLD:
            # Manual Threshold is inherently single-eye: detection runs on the
            # whole image, ROIs aren't eye-scoped. Force single-eye and disable
            # only the L/R radios so the Single Eye selection stays interactive.
            self._mt_saved_single_eye = self.single_eye_mode
            if not self.single_eye_mode:
                self._apply_single_eye_mode(True)
            eye_selector.left_eye_radio.setEnabled(False)
            eye_selector.right_eye_radio.setEnabled(False)
            params = panel.current_params()
            self._mt_pending_params = params
            # The detection that runs on mode entry just reflects the current
            # in-memory state — no user edit happened. Don't promote the
            # modified flag (it stays at whatever the user's prior edits set).
            self._mt_skip_next_modified_mark = True
            self._mt_debounce.start()
        else:
            self.image_viewer.set_manual_threshold_detection(None)
            # Leaving Manual Threshold mode also clears the ROI drag-edit state.
            panel.deactivate_roi_buttons()
            self.image_viewer.set_mt_active_roi(None)
            # Restore the eye-mode the user had before entering Manual Threshold.
            eye_selector.left_eye_radio.setEnabled(True)
            eye_selector.right_eye_radio.setEnabled(True)
            saved = getattr(self, "_mt_saved_single_eye", None)
            if saved is not None and saved != self.single_eye_mode:
                self._apply_single_eye_mode(saved)
        # Persist the new mode for this project.
        if self.project_dir is not None:
            project_settings = load_project_settings(self.project_dir)
            project_settings["current_mode"] = mode
            save_project_settings(self.project_dir, project_settings)

    def _on_pupil_roi_mode_changed(self, active: bool) -> None:
        """Activate or deactivate pupil-ROI drag editing on the image viewer."""
        self.image_viewer.set_mt_active_roi("pupil" if active else None)

    def _on_glint_roi_mode_changed(self, active: bool) -> None:
        """Activate or deactivate glint-ROI drag editing on the image viewer."""
        self.image_viewer.set_mt_active_roi("glint" if active else None)

    def _on_clear_pupil_roi(self) -> None:
        """Clear the pupil ROI and re-run detection without it."""
        self.image_viewer.clear_pupil_roi()
        self._kick_detection_if_active()

    def _on_clear_glint_roi(self) -> None:
        """Clear the glint ROI and re-run detection without it."""
        self.image_viewer.clear_glint_roi()
        self._kick_detection_if_active()

    def _on_mt_roi_changed(self, _roi: object) -> None:
        """Re-trigger detection after the user finishes dragging a Pupil/Glint ROI."""
        self._kick_detection_if_active()
        self.set_annotation_modified(True)

    def _kick_detection_if_active(self) -> None:
        """Schedule a debounced detection if Manual Threshold mode is on."""
        if self.annotation_controls.current_mode() != MODE_MANUAL_THRESHOLD:
            return
        self._mt_pending_params = self.annotation_controls.manual_threshold_panel.current_params()
        self._mt_debounce.start()

    def _on_image_loaded_for_manual_threshold(self) -> None:
        """Re-detect on the freshly loaded image if we're in Manual Threshold mode."""
        if self.annotation_controls.current_mode() != MODE_MANUAL_THRESHOLD:
            return
        self._mt_pending_params = self.annotation_controls.manual_threshold_panel.current_params()
        self._mt_debounce.start()

    def _on_manual_threshold_detection_ready(self, payload: dict) -> None:
        """Forward the worker's detection result into the image viewer.

        Mark the annotation as modified so navigating away triggers the
        save prompt (or autosave). Skipped exactly once after a tuning was
        restored from disk — that first re-run is in sync with the file.
        """
        self.image_viewer.set_manual_threshold_detection(payload)
        self.statusBar().clearMessage()
        if self._mt_skip_next_modified_mark:
            self._mt_skip_next_modified_mark = False
        else:
            self.set_annotation_modified(True)

    def _on_manual_threshold_detection_failed(self, message: str) -> None:
        """Surface a non-blocking detection error in the status bar."""
        self.image_viewer.set_manual_threshold_detection(None)
        self.statusBar().showMessage(f"Manual Threshold: {message}", 5000)

    def change_detector(self, detector_type: str, detector_name: str) -> None:
        """Change the active detector for a given type.

        Updates the global settings file (so the choice persists across
        sessions when no project is loaded) and, if a project is loaded,
        mirrors the choice into the per-project settings file so opening
        the same project later restores the same detectors.
        """
        self.settings_handler.set_setting(detector_type, detector_name)
        if self.project_dir is not None and detector_type in PROJECT_DETECTOR_KEYS:
            project_settings = load_project_settings(self.project_dir)
            project_settings[detector_type] = detector_name
            save_project_settings(self.project_dir, project_settings)
        self.menu_handler.update_menu_checks()

    def get_current_screen(self) -> QScreen | None:
        # Get the screen that contains the center of the window
        """Get the screen that currently contains the window."""
        center = self.geometry().center()
        return QApplication.screenAt(center)

    def resize_to_percentage(self, percentage: float) -> None:
        # Get the current screen
        """Resize the window to a percentage of screen size."""
        current_screen = self.get_current_screen()

        if current_screen:
            # Get the available geometry of the current screen
            available_geometry = current_screen.availableGeometry()

            # Calculate the new size
            new_width = int(available_geometry.width() * percentage)
            new_height = int(available_geometry.height() * percentage)

            # Calculate new position to keep the window centered on the current screen
            new_x = available_geometry.x() + (available_geometry.width() - new_width) // 2
            new_y = available_geometry.y() + (available_geometry.height() - new_height) // 2

            # Set the new geometry (position and size)
            new_geometry = QRect(new_x, new_y, new_width, new_height)
            self.setGeometry(new_geometry)

    def center_window(self) -> None:
        """Center the window on the current screen."""
        current_screen = self.get_current_screen()

        if current_screen:
            # Get the geometry of the current screen
            screen_geometry = current_screen.geometry()

            # Calculate the center point of the screen
            center_point = screen_geometry.center()

            # Move the window to the center of the current screen
            frame_geometry = self.frameGeometry()
            frame_geometry.moveCenter(center_point)
            self.move(frame_geometry.topLeft())

    def moveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle window move events."""
        # The current screen is updated automatically when the window moves
        super().moveEvent(event)

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:  # noqa: N802
        """Filter events for window state changes."""
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMaximized:
                # When maximized, keep it maximized (window controls visible)
                pass
            elif self.windowState() == Qt.WindowNoState:
                # When restored, set to 75% of current screen size
                self.resize_to_percentage(0.75)
        return super().eventFilter(obj, event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Handle window close event."""
        if self.annotation_modified:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Do you want to save before exiting?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )

            if reply == QMessageBox.Save:
                self.annotation_controller.save_annotations()
            elif reply == QMessageBox.Cancel:
                event.ignore()
                return

        # Stop the Manual Threshold worker thread before exiting so Qt
        # doesn't print "QThread: Destroyed while thread is still running".
        self._mt_thread.quit()
        self._mt_thread.wait()
        event.accept()

    @staticmethod
    def get_version_from_setup() -> str:
        """Get the application version from setup.py."""
        setup_path = Path(__file__).parent / ".." / ".." / "setup.py"
        tree = ast.parse(setup_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.func.id == "setup":
                for keyword in node.keywords:
                    if keyword.arg == "version":
                        return ast.literal_eval(keyword.value)
        return "Unknown"

    def show_about_dialog(self) -> None:
        """Show the about dialog with application information."""
        about_text = (
            "<h3>EyE Annotation Tool</h3>"
            "<p>A tool to annotate eye images for pupil, iris and eyelid detection.</p>"
            "<p>Developed by "
            "<a href='https://mh-salari.ir/'"
            "style='color: #8b7aa2;'>Mohammadhossein Salari</a></p>"
            f"<p>Current version: {self.get_version_from_setup()}</p>"
            "<p>To get the latest version of Eye Annotation Tool, visit<br>"
            "<a href='https://github.com/mh-salari/eye_annotation_tool' "
            "style='color: #8b7aa2;' target='_blank' rel='noopener noreferrer'>"
            "github.com/mh-salari/eye_annotation_tool</a></p>"
            "<p>This project has received funding from the European Union's Horizon "
            "Europe research and innovation funding program under grant "
            "agreement No 101072410, Eyes4ICU project.</p>"
        )
        # Create a custom widget for the about dialog
        about_widget = QWidget()
        layout = QVBoxLayout()

        # Add text
        text_label = QLabel(about_text)
        text_label.setTextFormat(Qt.RichText)
        text_label.setOpenExternalLinks(True)
        text_label.setWordWrap(True)
        layout.addWidget(text_label)

        # Add image
        image_label = QLabel()
        image_path = str(Path(__file__).parent / ".." / "resources" / "Funded_by_EU_Eyes4ICU.png")
        pixmap = QPixmap(image_path)
        image_label.setPixmap(pixmap.scaled(400, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        image_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(image_label)

        about_widget.setLayout(layout)

        # Create and show the message box without an icon
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("About EyE Annotation Tool")
        msg_box.setIcon(QMessageBox.NoIcon)  # This removes the icon
        msg_box.layout().addWidget(about_widget, 0, 0, 1, msg_box.layout().columnCount())
        msg_box.exec_()
