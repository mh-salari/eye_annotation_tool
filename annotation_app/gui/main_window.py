"""Main application window for the eye annotation tool."""

import ast
from pathlib import Path

from PyQt5.QtCore import QEvent, QRect, Qt, QTimer
from PyQt5.QtGui import QCloseEvent, QIcon, QPixmap, QScreen
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QRadioButton,
    QScrollArea,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..auto_detectors import PluginManager
from ..auto_detectors.orchestrator import DetectorOrchestrator
from ..auto_detectors.plugin_interface import DetectorPlugin, Target
from ..controllers.annotation_controller import AnnotationController
from ..controllers.navigation_controller import NavigationController
from ..utils.project_settings import DETECTOR_TARGETS, load_project_settings, save_project_settings
from .annotation_controls import MODE_AUTO_DETECT, MODE_MANUAL, AnnotationControlPanel
from .custom_widgets import MaterialButton
from .image_viewer import ImageViewer
from .menu_handler import MenuHandler
from .shortcut_handler import ShortcutHandler

# Slider-change → run_one debounce window. Slider drags fire many
# params_changed events per second; we collapse the burst to a single
# detector run ~100 ms after the last change so the orchestrator isn't
# flooded with stale intermediates.
AUTO_DETECT_DEBOUNCE_MS = 100

# Method-name suffix each plugin panel uses for its per-target ROI setter.
# Plugins that consume an ROI (currently ThresholdPupil) expose
# ``set_<target>_roi(roi)``; the orchestrator hands new rectangles back
# through that method so the panel's params dict stays the source of truth.
_PANEL_ROI_SETTER = "set_{target}_roi"


def _panel_roi_setter_name(target: str) -> str:
    """Return the panel-method name that pushes a new ROI for ``target``."""
    return _PANEL_ROI_SETTER.format(target=target)


def _panel_roi_param_key(target: str) -> str:
    """Return the params-dict key plugin panels use for their ``target`` ROI."""
    return f"{target}_roi"


class MainWindow(QMainWindow):
    """Main application window containing all UI components and controllers."""

    def __init__(self, cli_single_eye: bool = False) -> None:
        """Initialise the MainWindow.

        Args:
            cli_single_eye: When True, force single-eye mode on at startup
                regardless of any per-project setting.

        """
        super().__init__()
        self.setWindowTitle("EyE Annotation Tool")
        self.plugin_manager = PluginManager()
        self.orchestrator = DetectorOrchestrator(self)
        self._cli_single_eye = bool(cli_single_eye)
        self.single_eye_mode = self._cli_single_eye
        self.autosave_enabled = False
        # All folders loaded for the current session. Project-settings writes
        # propagate to every entry so any one of them can be reopened later
        # with the same configuration.
        self.project_dirs: list[str] = []

        # Resolved plugin instance per target, kept in sync with the current
        # project's "detectors" block. Targets whose project setting is
        # ``"disabled"`` are absent from this dict.
        self._enabled_plugins: dict[Target, DetectorPlugin] = {}

        # Buffered (plugin_name, params) pair for the next debounced run_one.
        # Cleared when the timer fires or when the user clicks Run Auto Detect.
        self._pending_run_one: tuple[str, dict] | None = None
        self._auto_detect_debounce = QTimer(self)
        self._auto_detect_debounce.setSingleShot(True)
        self._auto_detect_debounce.setInterval(AUTO_DETECT_DEBOUNCE_MS)

        self.setup_ui()
        self.setup_variables()

        self.annotation_controller = AnnotationController(self)
        self.navigation_controller = NavigationController(self)
        self.menu_handler = MenuHandler(self)
        self.shortcut_handler = ShortcutHandler(self)

        self.menu_handler.setup_menu()
        self.shortcut_handler.setup_shortcuts()
        self.connect_signals()

        icon_path = str(Path(__file__).parent / ".." / "resources" / "app_icon.ico")
        self.setWindowIcon(QIcon(icon_path))

        self.screen = QApplication.primaryScreen().availableGeometry()
        self.showMaximized()
        self.installEventFilter(self)

    def setup_ui(self) -> None:
        """Set up the user interface components."""
        central_widget = QWidget()
        main_layout = QHBoxLayout()

        # Left panel: load + navigate + save controls and the image list.
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

        self.image_viewer = ImageViewer()

        # Right panel for annotation controls. Wrapped in a QScrollArea so
        # taller Manual-mode content can't push the window past the screen.
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

        self.setStatusBar(QStatusBar())
        self.image_viewer.setFocus()

    def setup_variables(self) -> None:
        """Initialise instance variables."""
        self.image_paths: list[str] = []
        self.current_image_index = -1
        self.annotation_modified = False

    @property
    def project_dir(self) -> str | None:
        """Primary (first) loaded project dir, or None if no folders are loaded."""
        return self.project_dirs[0] if self.project_dirs else None

    def _save_to_all_projects(self, settings: dict) -> None:
        """Persist ``settings`` to every loaded project folder."""
        for project_dir in self.project_dirs:
            save_project_settings(project_dir, settings)

    def set_annotation_modified(self, modified: bool) -> None:
        """Set the annotation modified flag and refresh the GUI save-state indicator."""
        self.annotation_modified = modified
        self._refresh_save_state_indicator()

    def _refresh_save_state_indicator(self) -> None:
        """Sync the window title to the current save state.

        Treated as saved when autosave is enabled (autosave keeps disk in sync
        on every image change) or when no edits are pending.
        """
        saved = self.autosave_enabled or not self.annotation_modified
        if 0 <= self.current_image_index < len(self.image_paths):
            name = Path(self.image_paths[self.current_image_index]).name
            self.setWindowTitle(f"EyE Annotation Tool - {name}{'' if saved else ' *'}")
        else:
            self.setWindowTitle("EyE Annotation Tool" + ("" if saved else " *"))

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
        self.annotation_controls.clear_limbus_requested.connect(self.image_viewer.clear_limbus_points)
        self.annotation_controls.clear_eyelid_points_requested.connect(self.image_viewer.clear_eyelid_points)
        self.annotation_controls.clear_glint_points_requested.connect(self.image_viewer.clear_glint_points)
        self.annotation_controls.clear_all_requested.connect(self._on_clear_all)
        self.annotation_controls.mode_changed.connect(self._on_mode_changed)

        self.image_viewer.annotation_changed.connect(self.on_annotation_changed)
        self.image_viewer.annotation_type_changed.connect(self.annotation_controls.set_current_annotation)
        # On image change: drop the orchestrator's per-image cache before
        # the annotation_controller restores whatever the new image's saved
        # annotation carries. The image viewer clears its own per-image
        # overlay + target-ROI state inside ``load_image`` itself.
        self.image_viewer.image_loaded.connect(self.orchestrator.clear_cache)

        self.image_viewer.target_roi_changed.connect(self._on_target_roi_changed)

        self._auto_detect_debounce.timeout.connect(self._on_auto_detect_debounce_fired)
        self.orchestrator.plugin_ready.connect(self._on_plugin_ready)
        self.orchestrator.plugin_failed.connect(self._on_plugin_failed)

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
        """Pick a folder via dialog and load every supported image (non-recursive)."""
        folder = QFileDialog.getExistingDirectory(self, "Select Image Folder", "")
        if folder:
            self.load_folder_paths([folder])

    def load_folder_paths(self, folders: list[str]) -> None:
        """Load every supported image directly inside each of ``folders``.

        Listing is non-recursive — subdirectories are ignored. The aggregated
        image list is sorted lexicographically. If the loaded folders carry
        different project-settings files, the user is asked which to use and
        the chosen settings are written back to every folder.
        """
        if not folders:
            return
        suffixes = self.IMAGE_SUFFIXES
        seen: set[str] = set()
        for folder in folders:
            for p in Path(folder).iterdir():
                if p.is_file() and p.suffix.lower() in suffixes:
                    seen.add(str(p))
        image_paths = sorted(seen)
        if not image_paths:
            QMessageBox.warning(
                self,
                "No Images Found",
                "No image files found in: " + ", ".join(str(Path(f)) for f in folders),
            )
            return
        if not self._apply_project_settings([str(Path(f)) for f in folders]):
            return
        self.image_paths = image_paths
        self.current_image_index = 0
        self.update_image_list()
        self.load_current_image()

    def _load_project_settings_from(self, image_path: str) -> None:
        """Read the project settings file in the image's folder and apply it."""
        self._apply_project_settings([str(Path(image_path).parent)])

    def _apply_project_settings(self, project_dirs: list[str]) -> bool:
        """Apply project settings across ``project_dirs`` and propagate to all of them.

        Loads each folder's settings file; if non-empty configs differ, asks
        the user which one to use as the source (default = first). The chosen
        settings are immediately written to every folder so any of them can be
        reopened later with the same configuration.

        Returns ``False`` if the user cancels the chooser dialog; ``True``
        otherwise (including the no-conflict path).
        """
        if not project_dirs:
            self.project_dirs = []
            return True
        per_dir = {d: load_project_settings(d) for d in project_dirs}
        # Group folders by configuration. Stable order = order of first
        # appearance, so the first folder's settings stay the default.
        groups: list[tuple[dict, list[str]]] = []
        for d, settings in per_dir.items():
            for cfg, dirs in groups:
                if cfg == settings:
                    dirs.append(d)
                    break
            else:
                groups.append((settings, [d]))
        if len(groups) == 1:
            chosen = groups[0][0]
        else:
            chosen = self._choose_settings_among(groups)
            if chosen is None:
                return False
        self.project_dirs = list(project_dirs)
        self._save_to_all_projects(chosen)
        project_settings = chosen
        effective_single_eye = self._cli_single_eye or bool(project_settings.get("single_eye_mode", False))
        self._apply_single_eye_mode(effective_single_eye)
        self._apply_enabled_plugins(project_settings.get("detectors", {}))
        self.menu_handler.update_auto_detectors_menu()
        # Restore the last used mode for this project; setChecked fires the
        # button's toggled signal which drives _on_mode_changed.
        saved_mode = project_settings.get("current_mode", MODE_MANUAL)
        if saved_mode == MODE_AUTO_DETECT:
            self.annotation_controls.mode_auto_detect_button.setChecked(True)
        else:
            self.annotation_controls.mode_manual_button.setChecked(True)
        # Restore autosave toggle from the project file.
        autosave = bool(project_settings.get("autosave", False))
        self.autosave_enabled = autosave
        self.autosave_checkbox.blockSignals(True)
        self.autosave_checkbox.setChecked(autosave)
        self.autosave_checkbox.blockSignals(False)
        return True

    def _choose_settings_among(self, groups: list[tuple[dict, list[str]]]) -> dict | None:
        """Modal radio chooser when loaded folders carry different settings.

        ``groups`` is a list of ``(settings, [folders])`` tuples. Defaults to
        the first group. Returns the chosen settings dict, or ``None`` if the
        user cancels.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("Project settings differ")
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "The selected folders carry different project settings. Pick the\n"
                "configuration to use — it will be saved to all loaded folders.",
            ),
        )
        button_group = QButtonGroup(dialog)
        for i, (cfg, dirs) in enumerate(groups):
            preview_pairs = [f"{k}={v}" for k, v in cfg.items()]
            preview = ", ".join(preview_pairs) if preview_pairs else "(defaults)"
            radio = QRadioButton(f"{', '.join(Path(d).name for d in dirs)}\n    {preview}")
            if i == 0:
                radio.setChecked(True)
            button_group.addButton(radio, i)
            layout.addWidget(radio)
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        if dialog.exec_() != QDialog.Accepted:
            return None
        return groups[button_group.checkedId()][0]

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
        if self.project_dirs:
            settings = load_project_settings(self.project_dir)
            settings["single_eye_mode"] = self.single_eye_mode
            self._save_to_all_projects(settings)

    def _on_mode_changed(self, mode: str) -> None:
        """Persist the new mode and toggle which set of overlays the viewer paints.

        Manual mode shows the manual click-points + Annotate ROI; Auto
        Detect shows the per-target detection ellipses / centres + ROIs.
        Modes never overlap visually — the underlying data for both
        stays in memory regardless of which paint path is active.
        """
        is_auto = mode == MODE_AUTO_DETECT
        self.image_viewer.set_show_manual_annotations(not is_auto)
        self.image_viewer.set_show_detection_overlays(is_auto)
        if not self.project_dirs:
            return
        settings = load_project_settings(self.project_dir)
        settings["current_mode"] = mode
        self._save_to_all_projects(settings)

    def update_image_list(self) -> None:
        """Update the image list widget with current image paths.

        When a project folder is set, display each entry as its path relative
        to that root so files with the same basename in different subdirs are
        distinguishable.
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
        if self.project_dirs:
            project_settings = load_project_settings(self.project_dir)
            project_settings["autosave"] = enabled
            self._save_to_all_projects(project_settings)
        self._refresh_save_state_indicator()

    def collect_detections_for_save(self) -> dict:
        """Walk every enabled plugin and build the per-image ``detections`` dict.

        For each plugin whose target has a cached result on the current
        image, the dict gets one entry keyed by plugin name carrying both
        the parameter values used and the serialised result.
        """
        out: dict = {}
        for target, plugin in self._enabled_plugins.items():
            result = self.orchestrator.cached_result(target)
            if result is None:
                continue
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            params = panel.current_params() if panel is not None else plugin.default_params()
            out[plugin.name] = {
                "params": params,
                "result": plugin.serialize(result),
            }
        return out

    def apply_loaded_detections(self, detections: dict) -> None:
        """Restore per-image detection blocks from a loaded annotation file.

        For every block whose plugin is currently enabled, the panel is
        seeded with the saved params, the orchestrator's cache is primed
        with the deserialised result, and the viewer's overlay store
        receives the same result so the painter draws it. Blocks whose
        plugin is disabled or unknown for the current project are
        ignored — they will be re-saved as-is on the next save only if
        the user enables that plugin (handled by step g's UI).

        After the restore, every live cheap plugin is re-run once
        synchronously on the current image. The cached result is
        overwritten with the freshly computed one and the transient
        mask is populated — masks are stripped on serialise, so a
        freshly loaded image otherwise has no mask data even when its
        Show-mask toggle is on. Non-live plugins are not re-run; their
        own Detect button drives them.
        """
        for plugin_name, blob in detections.items():
            plugin = self.plugin_manager.get(plugin_name)
            if plugin is None:
                continue
            if self._enabled_plugins.get(plugin.target) is not plugin:
                continue
            params = blob.get("params") or {}
            result_blob = blob.get("result") or {}
            result = plugin.deserialize(result_blob)
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is not None:
                panel.set_params(params)
            self.orchestrator.set_cached_result(plugin.target, result)
            self.image_viewer.set_detection_overlay(plugin.target, result)
            # panel.set_params is silent by design; mirror the per-target
            # ROI value into the viewer's store so the rectangle renders.
            saved_roi = params.get(_panel_roi_param_key(plugin.target))
            self.image_viewer.set_target_roi(
                plugin.target,
                tuple(saved_roi) if saved_roi is not None else None,
            )
        self._refresh_live_plugin_results()

    def _refresh_live_plugin_results(self) -> None:
        """Re-run every enabled live plugin on the current image, in dep order.

        Walking ``DETECTOR_TARGETS`` in order respects the natural
        dependency chain (pupil → glint → limbus → eyelid) so a
        downstream plugin always sees the upstream one's freshly
        computed result in its ``shared_results`` dict. Non-live plugins
        are skipped — they only run on explicit user action.
        """
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return
        for target in DETECTOR_TARGETS:
            plugin = self._enabled_plugins.get(target)
            if plugin is None or not plugin.live:
                continue
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            self.orchestrator.run_one(target, image, panel.current_params())

    # ----- Auto Detectors menu: per-target plugin choice + project defaults -----

    def current_plugin_for_target(self, target: Target) -> str:
        """Return the slug of the plugin currently chosen for ``target`` (or ``"disabled"``)."""
        if not self.project_dirs:
            return "disabled"
        settings = load_project_settings(self.project_dir)
        return settings.get("detectors", {}).get(target, {}).get("plugin", "disabled")

    def select_plugin_for_target(self, target: Target, plugin_name: str) -> None:
        """Set ``target``'s plugin to ``plugin_name`` and rebuild the Auto Detect panels.

        ``plugin_name`` is either an existing plugin slug or the literal
        ``"disabled"``. Switching plugins resets the project-saved
        params for that target to the new plugin's
        :meth:`~DetectorPlugin.default_params` so old slider values for
        a different plugin do not leak across. Switching to the same
        plugin is a no-op.
        """
        if not self.project_dirs:
            self.statusBar().showMessage(
                "Load images first — detector choices live in the project settings file.",
                5000,
            )
            return
        settings = load_project_settings(self.project_dir)
        detectors = settings.setdefault("detectors", {})
        current = detectors.get(target, {}).get("plugin", "disabled")
        if current == plugin_name:
            return
        if plugin_name == "disabled":
            detectors[target] = {"plugin": "disabled", "params": {}}
        else:
            plugin = self.plugin_manager.get(plugin_name)
            if plugin is None or plugin.target != target:
                return
            detectors[target] = {"plugin": plugin_name, "params": plugin.default_params()}
        settings["detectors"] = detectors
        self._save_to_all_projects(settings)
        # Wipe per-image overlay / ROI / mask state for the target the
        # user is actually changing — the old plugin's result must not
        # leak into the new plugin's slot.
        self.image_viewer.clear_detection_overlay(target)
        self.image_viewer.set_target_roi(target, None)
        self.image_viewer.clear_target_mask(target)
        # Capture in-memory slider state of every other panel so the
        # rebuild does not silently revert their live tuning to the
        # project-saved values.
        preserved: dict[Target, dict] = {}
        for t, plugin in self._enabled_plugins.items():
            if t == target:
                continue
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is not None:
                preserved[t] = panel.current_params()
        self._apply_enabled_plugins(detectors, preserved_params=preserved)
        self.menu_handler.update_auto_detectors_menu()
        # Repopulate live plugin overlays + masks on the current image
        # so the user sees the new plugin's output without nudging a
        # slider.
        self._refresh_live_plugin_results()

    def save_current_settings_as_project_defaults(self) -> None:
        """Snapshot every enabled plugin's current panel params into project defaults.

        A confirmation dialog guards the action — the project file is
        about to be overwritten with the slider state from the Auto
        Detect panels.
        """
        if not self.project_dirs:
            QMessageBox.information(
                self,
                "No Project Loaded",
                "Load images first; project defaults are saved into the loaded folder's settings file.",
            )
            return
        if not self._enabled_plugins:
            QMessageBox.information(
                self,
                "No Detectors Enabled",
                "Enable at least one detector via the Auto Detectors menu before saving project defaults.",
            )
            return
        reply = QMessageBox.question(
            self,
            "Save Project Defaults?",
            "Replace this project's saved detector defaults with the current Auto Detect panel values?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        settings = load_project_settings(self.project_dir)
        detectors = settings.setdefault("detectors", {})
        for target, plugin in self._enabled_plugins.items():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            detectors[target] = {"plugin": plugin.name, "params": panel.current_params()}
        settings["detectors"] = detectors
        self._save_to_all_projects(settings)
        self.statusBar().showMessage("Project defaults saved.", 3000)

    # ----- Auto Detect mode: plugin resolution, run dispatch, signal forwarding -----

    def _apply_enabled_plugins(
        self,
        detectors_settings: dict,
        preserved_params: dict | None = None,
    ) -> None:
        """Resolve enabled plugins from project settings and (re)build the Auto Detect stack.

        ``detectors_settings`` is the ``"detectors"`` block from the project
        settings file: ``{target: {"plugin": name, "params": {...}}, ...}``.
        Targets whose plugin is ``"disabled"`` are skipped. An unknown
        plugin name raises ``RuntimeError`` — silent skipping would hide
        typos in the project file.

        ``preserved_params`` lets a caller override the project-file
        params for specific targets. Used by ``select_plugin_for_target``
        to keep the in-memory slider state of unchanged targets when the
        user toggles one detector via the menu — without this, every
        unchanged panel would silently snap back to whatever's on disk.

        The previous Auto Detect panel stack is replaced, its widgets are
        scheduled for deletion (their signal connections drop with them),
        and the orchestrator is refreshed via ``set_enabled_plugins``.
        """
        preserved_params = preserved_params or {}
        self._enabled_plugins = {}
        panels: list[tuple[str, QWidget]] = []
        for target in DETECTOR_TARGETS:
            entry = detectors_settings.get(target) or {}
            plugin_name = entry.get("plugin", "disabled")
            if plugin_name == "disabled":
                continue
            plugin = self.plugin_manager.get(plugin_name)
            if plugin is None:
                raise RuntimeError(
                    f"project settings reference unknown plugin {plugin_name!r} for target {target!r}; "
                    f"available: {sorted(self.plugin_manager.all())}",
                )
            if plugin.target != target:
                raise RuntimeError(
                    f"plugin {plugin_name!r} targets {plugin.target!r} but is configured for {target!r}",
                )
            self._enabled_plugins[target] = plugin
            panel = plugin.make_panel(self)
            initial_params = preserved_params.get(target, entry.get("params", {}))
            panel.set_params(initial_params)
            panel.params_changed.connect(
                # ``name`` and ``target`` are captured at connect time so the
                # closure stays valid even after the panel widget is replaced.
                lambda params, name=plugin.name, target_=plugin.target: self._on_plugin_params_changed(
                    name,
                    target_,
                    params,
                ),
            )
            # Wire the per-target ROI signals when the panel exposes them.
            # The plugin contract does not mandate ROI controls — only
            # plugins whose algorithm consumes an ROI (e.g. ThresholdPupil)
            # surface these signals.
            if hasattr(panel, "roi_edit_requested"):
                panel.roi_edit_requested.connect(
                    lambda checked, target_=plugin.target: self._on_panel_roi_edit_requested(
                        target_,
                        checked,
                    ),
                )
            if hasattr(panel, "clear_roi_requested"):
                panel.clear_roi_requested.connect(
                    lambda target_=plugin.target: self._on_panel_clear_roi_requested(target_),
                )
            if hasattr(panel, "show_mask_toggled"):
                panel.show_mask_toggled.connect(
                    lambda on, target_=plugin.target: self._on_panel_show_mask_toggled(target_, on),
                )
            if hasattr(panel, "detect_requested"):
                panel.detect_requested.connect(
                    lambda name=plugin.name, target_=plugin.target: self._on_panel_detect_requested(name, target_),
                )
            # Seed the viewer's ROI store from the project-saved params so
            # the rectangle is rendered immediately when Auto Detect mode
            # is entered.
            saved_roi = initial_params.get(_panel_roi_param_key(plugin.target))
            if saved_roi is not None:
                self.image_viewer.set_target_roi(plugin.target, tuple(saved_roi))
            panels.append((plugin.name, panel))
        self.annotation_controls.set_auto_detect_panels(panels)
        self.orchestrator.set_enabled_plugins(dict(self._enabled_plugins))

    def _on_plugin_params_changed(self, plugin_name: str, target: Target, params: dict) -> None:
        """Route a panel parameter change by the plugin's ``live`` flag.

        Live plugins re-run via the debounce path so slider drags collapse
        to a single ``run_one`` call. Non-live plugins (e.g. Daugman
        limbus) drop their cached result + overlay + mask immediately so
        the user is never looking at a stale visualisation that no longer
        matches the panel's current parameters; the next detection only
        happens when the user clicks the plugin's Detect button.
        """
        self.set_annotation_modified(True)
        plugin = self.plugin_manager.get(plugin_name)
        if plugin is None:
            return
        if plugin.live:
            self._pending_run_one = (plugin_name, dict(params))
            self._auto_detect_debounce.start()
            return
        self.orchestrator.set_cached_result(target, None)
        self.image_viewer.clear_detection_overlay(target)
        self.image_viewer.clear_target_mask(target)

    def _on_auto_detect_debounce_fired(self) -> None:
        """Dispatch the buffered run_one to the orchestrator.

        Skipped silently when no image grayscale is available (no image
        loaded yet) or the plugin was disabled between buffering and firing.
        """
        pending = self._pending_run_one
        self._pending_run_one = None
        if pending is None:
            return
        plugin_name, params = pending
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return
        plugin = self.plugin_manager.get(plugin_name)
        if plugin is None or self._enabled_plugins.get(plugin.target) is not plugin:
            return
        self.orchestrator.run_one(plugin.target, image, params)

    def _on_plugin_ready(self, target: str, result: dict) -> None:
        """Render the new detection result + its optional mask, mark modified."""
        # The mask (if any) lives under the standard ``"mask"`` key per
        # the plugin contract — split it off from the geometry overlay so
        # the viewer's mask renderer and its detection renderer stay on
        # their own paths.
        self.image_viewer.set_target_mask(target, result.get("mask"))
        self.image_viewer.set_detection_overlay(target, result)
        self.set_annotation_modified(True)

    def _on_plugin_failed(self, target: str) -> None:
        """Clear the per-target overlay + mask and surface the failure in the status bar."""
        self.image_viewer.clear_detection_overlay(target)
        self.image_viewer.clear_target_mask(target)
        self.statusBar().showMessage(f"Auto Detect: {target} failed at current parameters.", 5000)

    def _on_clear_all(self) -> None:
        """Route the Clear All button by current mode.

        Manual mode clears every manual annotation type (pupil / limbus /
        eyelid / glint points). Auto Detect mode resets every mounted
        plugin panel to its defaults, drops the orchestrator's cached
        results, clears the viewer's overlays and per-target ROIs, and
        cancels any in-flight debounced re-run. No detection runs — the
        user clicks Run Auto Detect when ready.
        """
        if self.annotation_controls.current_mode() == MODE_AUTO_DETECT:
            self._clear_all_auto_detect()
        else:
            self.image_viewer.clear_all()

    def _clear_all_auto_detect(self) -> None:
        """Reset every Auto Detect plugin panel, the orchestrator cache, and the viewer state."""
        # Cancel any debounced run_one that might fire mid-reset.
        self._auto_detect_debounce.stop()
        self._pending_run_one = None

        for target, plugin in self._enabled_plugins.items():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            # set_params is silent — the panel updates its widgets without
            # firing params_changed, so the orchestrator does not see a
            # change request and detection stays off until the user asks.
            panel.set_params(plugin.default_params())
            self.orchestrator.set_cached_result(target, None)
            self.image_viewer.clear_detection_overlay(target)
            self.image_viewer.clear_target_mask(target)
            self.image_viewer.set_target_roi(target, None)
        # Active drag-edit target was likely tied to one of the panels we
        # just reset; drop it so the canvas isn't waiting on a phantom drag.
        self.image_viewer.set_active_roi_target(None)
        self.set_annotation_modified(True)

    def _on_panel_detect_requested(self, plugin_name: str, target: Target) -> None:
        """Run a non-live plugin once on the current image.

        Wired to the plugin panel's Detect button. The orchestrator
        invokes ``detect`` synchronously and emits ``plugin_ready`` (or
        ``plugin_failed``) which lands the new overlay via the usual
        ``_on_plugin_ready`` path.
        """
        plugin = self._enabled_plugins.get(target)
        if plugin is None or plugin.name != plugin_name:
            return
        panel = self.annotation_controls.auto_detect_panel(plugin_name)
        if panel is None:
            return
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return
        self.orchestrator.run_one(target, image, panel.current_params())

    def _on_panel_show_mask_toggled(self, target: str, on: bool) -> None:
        """Forward the panel's "Show mask" toggle to the viewer.

        Masks are transient: a loaded image has no mask until a plugin
        runs against it. Nudging any slider re-runs the cheap plugins
        and populates the mask; nothing implicit happens on toggle.
        """
        self.image_viewer.set_show_target_mask(target, on)

    def _on_panel_roi_edit_requested(self, target: str, active: bool) -> None:
        """Enter (or leave) drag-edit mode for ``target``'s ROI on the canvas."""
        self.image_viewer.set_active_roi_target(target if active else None)

    def _on_panel_clear_roi_requested(self, target: str) -> None:
        """Drop ``target``'s ROI everywhere: viewer store, panel params, plus re-run."""
        self.image_viewer.set_target_roi(target, None)
        plugin = self._enabled_plugins.get(target)
        if plugin is None:
            return
        panel = self.annotation_controls.auto_detect_panel(plugin.name)
        setter = getattr(panel, _panel_roi_setter_name(target), None) if panel is not None else None
        if setter is not None:
            # set_<target>_roi(None) updates the panel's params dict and emits
            # params_changed → the debounce path re-runs detection without
            # the ROI constraint.
            setter(None)

    def _on_target_roi_changed(self, target: str, roi: tuple | None) -> None:
        """Push a canvas-edited ROI back into the panel's params dict.

        The panel's setter (e.g. ``set_pupil_roi``) writes the new value
        into the params dict and emits ``params_changed``; the regular
        debounce path then re-runs detection with the updated ROI.
        """
        plugin = self._enabled_plugins.get(target)
        if plugin is None:
            return
        panel = self.annotation_controls.auto_detect_panel(plugin.name)
        setter = getattr(panel, _panel_roi_setter_name(target), None) if panel is not None else None
        if setter is not None:
            setter(roi)

    def get_current_screen(self) -> QScreen | None:
        """Get the screen that currently contains the window."""
        center = self.geometry().center()
        return QApplication.screenAt(center)

    def resize_to_percentage(self, percentage: float) -> None:
        """Resize the window to ``percentage`` of the current screen's geometry."""
        current_screen = self.get_current_screen()
        if current_screen:
            available_geometry = current_screen.availableGeometry()
            new_width = int(available_geometry.width() * percentage)
            new_height = int(available_geometry.height() * percentage)
            new_x = available_geometry.x() + (available_geometry.width() - new_width) // 2
            new_y = available_geometry.y() + (available_geometry.height() - new_height) // 2
            new_geometry = QRect(new_x, new_y, new_width, new_height)
            self.setGeometry(new_geometry)

    def center_window(self) -> None:
        """Centre the window on the current screen."""
        current_screen = self.get_current_screen()
        if current_screen:
            screen_geometry = current_screen.geometry()
            center_point = screen_geometry.center()
            frame_geometry = self.frameGeometry()
            frame_geometry.moveCenter(center_point)
            self.move(frame_geometry.topLeft())

    def moveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Handle window move events."""
        super().moveEvent(event)

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:  # noqa: N802
        """Filter events for window state changes."""
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMaximized:
                pass
            elif self.windowState() == Qt.WindowNoState:
                # When restored from maximised, set to 75% of the current screen.
                self.resize_to_percentage(0.75)
        return super().eventFilter(obj, event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Handle the window close event with autosave + unsaved-changes prompt."""
        if self.annotation_modified:
            if self.autosave_enabled:
                self.annotation_controller.save_annotations()
            else:
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
        event.accept()

    @staticmethod
    def get_version_from_setup() -> str:
        """Read the application version literal from ``setup.py``."""
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
            "<p>A tool to annotate eye images for pupil, limbus and eyelid detection.</p>"
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
        about_widget = QWidget()
        layout = QVBoxLayout()
        text_label = QLabel(about_text)
        text_label.setTextFormat(Qt.RichText)
        text_label.setOpenExternalLinks(True)
        text_label.setWordWrap(True)
        layout.addWidget(text_label)
        image_label = QLabel()
        image_path = str(Path(__file__).parent / ".." / "resources" / "Funded_by_EU_Eyes4ICU.png")
        pixmap = QPixmap(image_path)
        image_label.setPixmap(pixmap.scaled(400, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        image_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(image_label)
        about_widget.setLayout(layout)
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("About EyE Annotation Tool")
        msg_box.setIcon(QMessageBox.NoIcon)
        msg_box.layout().addWidget(about_widget, 0, 0, 1, msg_box.layout().columnCount())
        msg_box.exec_()
