"""Main application window for the eye annotation tool."""

import ast
from pathlib import Path

import qtawesome as qta
from PyQt5.QtCore import QEvent, QRect, QSize, Qt
from PyQt5.QtGui import QCloseEvent, QIcon, QPixmap, QScreen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
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
from ..auto_detectors.orchestrator import DetectorOrchestrator
from ..controllers.annotation_controller import AnnotationController
from ..controllers.binocular_controller import BinocularController
from ..controllers.detection_controller import DetectionController
from ..controllers.navigation_controller import NavigationController
from ..policy import CliOverridePolicy
from ..state import CarryRoiStore, PerEyeStateStore, ProjectStore
from ..utils.project_settings import (
    DETECTOR_TARGETS,
    PROJECT_FILE_SUFFIX,
)
from .annotation_controls import MODE_AUTO_DETECT, MODE_MANUAL, AnnotationControlPanel
from .custom_widgets import MaterialButton
from .image_viewer import ImageViewer
from .menu_handler import MenuHandler
from .shortcut_handler import ShortcutHandler


class MainWindow(QMainWindow):
    """Main application window containing all UI components and controllers."""

    def __init__(
        self,
        cli_monocular: bool = False,
        cli_auto_detectors: set[str] | None = None,
    ) -> None:
        """Initialise the MainWindow.

        Args:
            cli_monocular: When True, force monocular mode on at startup
                regardless of any per-project setting (i.e. the image is
                treated as a single eye with no left/right split).
            cli_auto_detectors: When given, restrict Auto Detect to this
                subset of ``DETECTOR_TARGETS`` for the whole session;
                targets not in the set are forced to ``"disabled"``
                regardless of the per-project detector choices. ``None``
                (the default) defers to the project file.

        """
        super().__init__()
        self.setWindowTitle("EyE Annotation Tool")
        self.plugin_manager = PluginManager()
        self.orchestrator = DetectorOrchestrator(self)
        self.cli_policy = CliOverridePolicy(cli_monocular, cli_auto_detectors)
        self.project_store = ProjectStore()
        self.per_eye_state = PerEyeStateStore(DETECTOR_TARGETS)
        self.carry_roi_state = CarryRoiStore(DETECTOR_TARGETS)

        self.setup_ui()
        self.setup_variables()

        self.detection_controller = DetectionController(
            self.plugin_manager,
            self.orchestrator,
            self.per_eye_state,
            self.carry_roi_state,
            self.project_store,
            self.image_viewer,
            self.annotation_controls,
            active_slot_fn=lambda: self.binocular_controller.active_eye_slot(),
            binocular_mode_fn=lambda: self.binocular_controller.is_binocular,
            effective_divider_fn=lambda: self.binocular_controller.effective_divider_x_norm(),
            parent=self,
        )
        self.detection_controller.annotation_modified.connect(self.set_annotation_modified)
        self.detection_controller.status_message.connect(self.statusBar().showMessage)
        self.detection_controller.detectors_changed.connect(self._on_detectors_changed)

        self.binocular_controller = BinocularController(
            self.image_viewer,
            self.annotation_controls,
            self.per_eye_state,
            self.cli_policy,
            self.project_store,
            self.detection_controller,
            current_image_path_fn=self._current_image_path,
            orchestrator=self.orchestrator,
            initial_binocular=not self.cli_policy.monocular,
            parent=self,
        )
        self.binocular_controller.apply_mode(not self.cli_policy.monocular)
        self.binocular_controller.annotation_modified.connect(self.set_annotation_modified)

        self.annotation_controller = AnnotationController(
            self.image_viewer,
            self.detection_controller,
            self.binocular_controller,
            self.project_store,
            current_index_fn=lambda: self.current_image_index,
            is_modified_fn=lambda: self.annotation_modified,
            set_modified_fn=self.set_annotation_modified,
            dialog_parent=self,
        )
        self.navigation_controller = NavigationController(
            self.annotation_controller,
            self.project_store,
            self.image_list_widget,
            self.load_current_image,
            current_index_getter=lambda: self.current_image_index,
            current_index_setter=self._set_current_image_index,
            is_modified_fn=lambda: self.annotation_modified,
            dialog_parent=self,
        )
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
        self.load_folder_button = MaterialButton("Load Images from Folder")
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
        self.image_list_widget.setSelectionMode(QListWidget.ExtendedSelection)
        left_layout.addWidget(QLabel("Loaded Images:"))
        left_layout.addWidget(self.image_list_widget)

        # Trash button below the image list: drops selected entries from the
        # project's image set (files on disk are untouched).
        icon_colour_for_trash = "#e0e0e0"
        self.remove_images_button = MaterialButton("  Remove from project", compact=True)
        self.remove_images_button.setIcon(qta.icon("mdi6.trash-can-outline", color=icon_colour_for_trash))
        self.remove_images_button.setIconSize(QSize(18, 18))
        self.remove_images_button.setToolTip("Drop the selected images from the project (files on disk are kept).")
        left_layout.addWidget(self.remove_images_button)

        left_layout.addStretch(1)

        # Zoom + display-brightness controls pinned at the bottom of the
        # left panel — fixed rows so they stay reachable regardless of
        # how long the loaded-images list grows. Brightness only changes
        # the canvas; detector plugins keep seeing the unmodified source.
        # Icons are Material Design Icons via qtawesome (mdi6.*); tooltips
        # carry the full label so the icon-only buttons stay accessible.
        icon_colour = "#e0e0e0"
        icon_size = QSize(18, 18)
        zoom_row = QHBoxLayout()
        zoom_row.setContentsMargins(0, 0, 0, 0)
        self.zoom_in_button = MaterialButton("  +", compact=True)
        self.zoom_in_button.setIcon(qta.icon("mdi6.magnify-plus-outline", color=icon_colour))
        self.zoom_in_button.setIconSize(icon_size)
        self.zoom_in_button.setToolTip("Zoom in")
        self.zoom_out_button = MaterialButton("  -", compact=True)
        self.zoom_out_button.setIcon(qta.icon("mdi6.magnify-minus-outline", color=icon_colour))
        self.zoom_out_button.setIconSize(icon_size)
        self.zoom_out_button.setToolTip("Zoom out")
        self.zoom_reset_button = MaterialButton("  reset", compact=True)
        self.zoom_reset_button.setIcon(qta.icon("mdi6.fit-to-screen-outline", color=icon_colour))
        self.zoom_reset_button.setIconSize(icon_size)
        self.zoom_reset_button.setToolTip("Fit image to viewport")
        zoom_row.addWidget(self.zoom_in_button)
        zoom_row.addWidget(self.zoom_out_button)
        zoom_row.addWidget(self.zoom_reset_button)
        left_layout.addLayout(zoom_row)
        brightness_row = QHBoxLayout()
        brightness_row.setContentsMargins(0, 0, 0, 0)
        # The three sun icons differ only in subtle ray count; the +, -,
        # 'reset' text suffixes are what actually communicates the
        # direction at a glance. Tooltips spell out the full label.
        self.brighter_button = MaterialButton("  +", compact=True)
        self.brighter_button.setIcon(qta.icon("mdi6.brightness-5", color=icon_colour))
        self.brighter_button.setIconSize(icon_size)
        self.brighter_button.setToolTip("Brighten the displayed image")
        self.darker_button = MaterialButton("  -", compact=True)
        self.darker_button.setIcon(qta.icon("mdi6.brightness-4", color=icon_colour))
        self.darker_button.setIconSize(icon_size)
        self.darker_button.setToolTip("Darken the displayed image")
        self.brightness_reset_button = MaterialButton("  reset", compact=True)
        self.brightness_reset_button.setIcon(qta.icon("mdi6.brightness-6", color=icon_colour))
        self.brightness_reset_button.setIconSize(icon_size)
        self.brightness_reset_button.setToolTip("Restore original brightness")
        brightness_row.addWidget(self.brighter_button)
        brightness_row.addWidget(self.darker_button)
        brightness_row.addWidget(self.brightness_reset_button)
        left_layout.addLayout(brightness_row)
        left_panel.setLayout(left_layout)

        self.image_viewer = ImageViewer()
        self.zoom_in_button.clicked.connect(self.image_viewer.zoom_in_centered)
        self.zoom_out_button.clicked.connect(self.image_viewer.zoom_out_centered)
        self.zoom_reset_button.clicked.connect(self.image_viewer.reset_zoom_to_fit)
        self.brighter_button.clicked.connect(self.image_viewer.brighten_display)
        self.darker_button.clicked.connect(self.image_viewer.darken_display)
        self.brightness_reset_button.clicked.connect(self.image_viewer.reset_display_brightness)

        # Right panel: AnnotationControlPanel inside a QScrollArea so taller
        # Auto Detect plugin stacks scroll instead of pushing the window
        # past the screen. Clear All sits below the scroll area as a fixed
        # footer so it stays visible regardless of how tall the panel
        # contents grow.
        self.annotation_controls = AnnotationControlPanel()
        right_scroll = QScrollArea()
        right_scroll.setWidget(self.annotation_controls)
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setFrameShape(QScrollArea.NoFrame)

        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(right_scroll, 1)
        right_layout.addWidget(self.annotation_controls.clear_all_button)
        right_panel.setLayout(right_layout)
        right_panel.setFixedWidth(360)  # 340 panel + room for the vertical scrollbar

        main_layout.addWidget(left_panel)
        main_layout.addWidget(self.image_viewer, 1)
        main_layout.addWidget(right_panel)

        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        self.setStatusBar(QStatusBar())
        self.image_viewer.setFocus()

    def setup_variables(self) -> None:
        """Initialise instance variables."""
        self.current_image_index = -1
        self.annotation_modified = False

    @property
    def image_paths(self) -> list[str]:
        """Ordered list of image paths in the current project."""
        return self.project_store.image_paths()

    @property
    def autosave_enabled(self) -> bool:
        """Autosave-on-image-change flag (read by NavigationController)."""
        return self.project_store.autosave

    @property
    def project_divider_x_norm(self) -> float:
        """Project-wide default divider position."""
        return self.project_store.divider_x_norm

    @property
    def binocular_mode(self) -> bool:
        """True when the active project is in binocular mode (read by external code)."""
        return self.binocular_controller.is_binocular

    def _current_image_path(self) -> str | None:
        """Return the active image's path, or ``None`` when no image is loaded."""
        if 0 <= self.current_image_index < len(self.image_paths):
            return self.image_paths[self.current_image_index]
        return None

    def _set_current_image_index(self, index: int) -> None:
        """Setter exposed to :class:`NavigationController` so the index lives here."""
        self.current_image_index = index

    def set_annotation_modified(self, modified: bool) -> None:
        """Set the annotation modified flag and refresh the GUI save-state indicator."""
        self.annotation_modified = modified
        self._refresh_save_state_indicator()

    def _refresh_save_state_indicator(self) -> None:
        """Sync the window title to the current save state.

        Treated as saved when autosave is enabled (autosave keeps disk in sync
        on every image change) or when no edits are pending. Read-only
        sessions are tagged with a ``(read-only)`` suffix so the user can
        see at a glance that project-level edits won't persist.
        """
        saved = self.autosave_enabled or not self.annotation_modified
        ro = " (read-only)" if self.project_store.read_only else ""
        if 0 <= self.current_image_index < len(self.image_paths):
            name = Path(self.image_paths[self.current_image_index]).name
            self.setWindowTitle(f"EyE Annotation Tool - {name}{'' if saved else ' *'}{ro}")
        else:
            self.setWindowTitle(f"EyE Annotation Tool{'' if saved else ' *'}{ro}")

    def connect_signals(self) -> None:
        """Connect signals and slots for UI components."""
        self.load_images_button.clicked.connect(self.on_load_images_clicked)
        self.load_folder_button.clicked.connect(self.on_load_folder_clicked)
        self.prev_image_button.clicked.connect(self.navigation_controller.prev_image)
        self.next_image_button.clicked.connect(self.navigation_controller.next_image)
        self.save_annotations_button.clicked.connect(self.annotation_controller.save_annotations)
        self.remove_images_button.clicked.connect(self.remove_selected_images)
        self.image_list_widget.itemClicked.connect(self.navigation_controller.on_image_selected)
        self.image_list_widget.installEventFilter(self)

        self.annotation_controls.annotation_changed.connect(self.image_viewer.set_current_annotation)
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
        # On image change: drop both the orchestrator's per-image cache
        # AND the per-eye snapshot before the annotation_controller
        # restores whatever the new image's saved annotation carries.
        # The image viewer clears its own per-image overlay + target-ROI
        # state inside ``load_image`` itself.
        self.image_viewer.image_loaded.connect(self._on_image_loaded)

    IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp")

    # ----- Project lifecycle (new / open / save / save-as) -------------

    def new_project(self, project_path: str, initial_project: dict | None = None) -> None:
        """Create a brand-new project file at ``project_path`` and load it.

        ``initial_project`` lets the New Project wizard pre-fill detector
        choices, binocular mode, etc.; missing keys fall back to
        :func:`default_project`. The file is written to disk immediately
        and becomes the active session project.
        """
        self.project_store.new(project_path, initial_project)
        self._apply_project_state()
        self.current_image_index = 0 if self.image_paths else -1
        self.update_image_list()
        if self.image_paths:
            self.load_current_image()

    def open_project(self, project_path: str) -> None:
        """Load ``project_path`` from disk and apply it as the active project."""
        self.project_store.load(project_path)
        self._apply_project_state()
        self.current_image_index = 0 if self.image_paths else -1
        self.update_image_list()
        if self.image_paths:
            self.load_current_image()

    def open_project_for_review(self, project_path: str, image_paths: list[str]) -> None:
        """Open ``project_path`` in read-only mode with a supplied image list.

        The project's settings (binocular flag, divider, detector plugins +
        params, autosave) are loaded as usual, but the in-memory image list
        is replaced with ``image_paths``. Every subsequent edit (slider
        changes, image list edits, divider drags) stays in memory for the
        session only — ``ProjectStore.persist`` is a no-op while the
        store's ``read_only`` flag is set. Per-image annotation files
        still save next to their PNGs.

        The user can still snapshot the session to a different file via
        File > Save Project As…; ``save_project_as`` clears the read-only
        flag (the snapshot becomes the new active project).
        """
        self.project_store.load_for_review(project_path, image_paths)
        self._apply_project_state()
        self.current_image_index = 0 if self.image_paths else -1
        self.update_image_list()
        if self.image_paths:
            self.load_current_image()
        self._refresh_save_state_indicator()

    def save_project(self) -> None:
        """Write the active project to disk; prompt for path if unsaved.

        Refuses to overwrite the active path in read-only mode — re-routes
        to :meth:`save_project_as` so the user must pick a fresh path. This
        keeps Save Project's quick-write semantics from silently mutating
        a project a read-only session was meant to leave alone.
        """
        if self.project_store.path is None or self.project_store.read_only:
            self.save_project_as()
            return
        self.project_store.save()

    def save_project_as(self) -> None:
        """Prompt for a save path and persist the active project there.

        Snapshotting clears the read-only flag: the chosen path becomes
        the new active project, and subsequent edits write through to it
        normally.
        """
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project As",
            "",
            f"Project Files (*{PROJECT_FILE_SUFFIX})",
        )
        if not path:
            return
        if not path.endswith(PROJECT_FILE_SUFFIX):
            path = path + PROJECT_FILE_SUFFIX
        self.project_store.save_as(path)
        self._refresh_save_state_indicator()

    def add_images(self, image_paths: list[str]) -> None:
        """Append ``image_paths`` to the project's images dict.

        Already-present paths are skipped. Non-image / missing files are
        filtered out and surfaced via a single info dialog so callers
        don't need to pre-validate.
        """
        suffixes = self.IMAGE_SUFFIXES
        missing = [str(p) for p in image_paths if not Path(p).is_file()]
        valid = [str(Path(p)) for p in image_paths if Path(p).is_file() and Path(p).suffix.lower() in suffixes]
        if not valid:
            QMessageBox.warning(
                self,
                "No Images Loaded",
                "None of the supplied paths resolved to a supported image file. "
                + (f"Missing or unreadable: {missing}" if missing else ""),
            )
            return
        if missing:
            QMessageBox.information(
                self,
                "Some Images Skipped",
                "Some supplied paths were not loadable images:\n  - " + "\n  - ".join(missing),
            )
        had_any_before = bool(self.image_paths)
        self.project_store.add_images(valid)
        if not had_any_before:
            self.current_image_index = 0
        self.update_image_list()
        if self.image_paths and self.current_image_index < 0:
            self.current_image_index = 0
        self.load_current_image()

    def add_images_from_folder(self, folder: str) -> None:
        """Append every supported image directly inside ``folder`` (non-recursive)."""
        suffixes = self.IMAGE_SUFFIXES
        found = sorted(str(p) for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in suffixes)
        if not found:
            QMessageBox.warning(
                self,
                "No Images Found",
                f"No image files found in: {folder}",
            )
            return
        self.add_images(found)

    def remove_selected_images(self) -> None:
        """Drop the currently-selected image-list rows from the project's image set.

        Files on disk are untouched — only the project's image dict is
        mutated. Triggered by the trash icon below the image list and by
        the Delete / Backspace key on the list widget.
        """
        selected = self.image_list_widget.selectedIndexes()
        if not selected:
            return
        paths_to_remove = [self.image_paths[idx.row()] for idx in selected if 0 <= idx.row() < len(self.image_paths)]
        if not paths_to_remove:
            return
        current_path = (
            self.image_paths[self.current_image_index]
            if 0 <= self.current_image_index < len(self.image_paths)
            else None
        )
        self.project_store.remove_images(paths_to_remove)
        if current_path in paths_to_remove or not self.image_paths:
            self.current_image_index = 0 if self.image_paths else -1
        else:
            self.current_image_index = self.image_paths.index(current_path)
        self.update_image_list()
        if self.image_paths:
            self.load_current_image()
        else:
            self.image_viewer.clear()

    # ----- Apply project state to the rest of the UI -------------------

    def _apply_project_state(self) -> None:
        """Push the active project's settings into the dependent widgets.

        Called once after every project load (new / open). Mid-session
        mutations go through :class:`ProjectStore` setters which persist
        on the spot; this method only runs on full project swaps.
        """
        project = self.project_store.project
        self._resolve_cli_overrides_policy(project)
        project["binocular_mode"] = self.cli_policy.session_binocular(self.project_store.binocular_mode)
        project["detectors"] = self.cli_policy.session_detectors(project.get("detectors", {}))
        if self.cli_policy.is_active():
            self.project_store.persist()
        self.binocular_controller.apply_mode(self.project_store.binocular_mode)
        self.image_viewer.set_divider_x_norm(self.binocular_controller.effective_divider_x_norm())
        self.detection_controller.apply_enabled_plugins(project.get("detectors", {}))
        self.menu_handler.update_auto_detectors_menu()
        if self.project_store.current_mode == MODE_AUTO_DETECT:
            self.annotation_controls.mode_auto_detect_button.setChecked(True)
        else:
            self.annotation_controls.mode_manual_button.setChecked(True)
        self.autosave_checkbox.blockSignals(True)
        self.autosave_checkbox.setChecked(self.project_store.autosave)
        self.autosave_checkbox.blockSignals(False)

    # ----- File-menu action stubs (wired by MenuHandler) ---------------

    def on_new_project(self) -> None:
        """File > New Project — show the wizard, then create + load the project."""
        from .new_project_dialog import NewProjectDialog  # local import: GUI-only

        dialog = NewProjectDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        result = dialog.result_payload()
        self.new_project(result["path"], result["project"])

    def on_open_project(self) -> None:
        """File > Open Project — pick a file, load it."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            "",
            f"Project Files (*{PROJECT_FILE_SUFFIX})",
        )
        if path:
            self.open_project(path)

    def on_load_images_clicked(self) -> None:
        """Left-panel "Load Images" button — file picker, appends to project."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Image Files",
            "",
            "Image Files (*.png *.jpg *.bmp)",
        )
        if files:
            self.add_images(files)

    def on_load_folder_clicked(self) -> None:
        """Left-panel "Load Images from Folder" button — folder picker, appends to project."""
        folder = QFileDialog.getExistingDirectory(self, "Select Image Folder", "")
        if folder:
            self.add_images_from_folder(folder)

    # ----- CLI override session policy ---------------------------------

    def _resolve_cli_overrides_policy(self, project_settings: dict) -> None:
        """Decide whether CLI flags win for this session.

        With no CLI flags, the policy stays inactive — applying overrides
        is a no-op. With CLI flags but no conflict, the policy activates
        silently. With CLI flags that disagree with the project file,
        show a dialog so the user picks which side wins.
        """
        if not self.cli_policy.has_any_override():
            self.cli_policy.set_active(False)
            return
        conflicts = self.cli_policy.conflicts(project_settings)
        if not conflicts:
            self.cli_policy.set_active(True)
            return
        self.cli_policy.set_active(self._ask_cli_overrides_dialog(conflicts))

    def _ask_cli_overrides_dialog(self, conflicts: list[str]) -> bool:
        """Prompt: should the CLI flags override (and persist over) the project file?

        Yes -> CLI wins for the session AND the project settings file is
        rewritten with the CLI values. No -> project file wins, CLI
        flags are ignored for the session.
        """
        body = (
            "Your CLI flags disagree with the loaded project file:\n\n  - "
            + "\n  - ".join(conflicts)
            + "\n\nApply the CLI flags AND save them to the project file?\n"
            "(Yes = overwrite the project file with the CLI values; "
            "No = keep the project file as-is and ignore the CLI flags.)"
        )
        reply = QMessageBox.question(
            self,
            "CLI flags vs. project settings",
            body,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return reply == QMessageBox.Yes

    def _on_image_loaded(self) -> None:
        """Drop orchestrator cache + per-eye snapshots when a new image lands."""
        self.orchestrator.clear_cache()
        self.per_eye_state.clear_all()

    def _on_mode_changed(self, mode: str) -> None:
        """Persist the new mode and cancel any Auto-Detect ROI drag state.

        Manual annotations and detection overlays both paint regardless of
        the active mode; visibility is decided per-target by which side
        owns that target (manual or an auto detector). The mode switcher
        only controls which panel is on the right and which side of the
        per-target data the user is allowed to edit.

        Switching to Manual cancels any ROI drag-edit toggle the user
        left active on an Auto Detect panel so canvas clicks in Manual
        mode aren't intercepted by a stale ROI drag handler.
        """
        if mode == MODE_MANUAL:
            self.detection_controller.cancel_active_roi_edit()
        # Manual click-to-place and click-to-edit on the canvas are
        # only allowed in Manual mode. Auto Detect mode still paints
        # manual annotations so the user can see them, but blocks edits
        # until they switch back.
        self.image_viewer.set_manual_edit_enabled(mode == MODE_MANUAL)
        self.project_store.current_mode = mode

    def update_image_list(self) -> None:
        """Update the image list widget with current image paths.

        When a project file is set, display each entry as its path relative
        to the project file's directory so files with the same basename in
        different subdirs are distinguishable. Falls back to bare filenames
        when the project hasn't been saved yet.
        """
        self.image_list_widget.clear()
        project_root = self.project_store.project_root()
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

    def on_annotation_changed(self) -> None:
        """Handle a manual-annotation edit: mark dirty and republish the manual pupil.

        Manual pupil ellipse changes are forwarded to the detection
        controller so it can refresh the synthetic pupil cache and
        re-run downstream live plugins (glint, limbus) against the
        new centre / radius.
        """
        self.set_annotation_modified(True)
        self.detection_controller.on_manual_annotation_changed()

    def _on_autosave_changed(self, enabled: bool) -> None:
        """Persist the autosave toggle in project settings."""
        self.project_store.autosave = enabled
        self._refresh_save_state_indicator()

    def _on_clear_all(self) -> None:
        """Wipe every manual annotation AND every Auto Detect result on the current image.

        Clear All is mode-agnostic by design: it drops the manual
        point/ellipse sets across both eyes and resets every mounted
        plugin panel + cached detection + overlay + mask + ROI. No
        detection re-runs after the clear.
        """
        self.image_viewer.clear_all()
        self.detection_controller.clear_all_auto_detect()

    def _on_detectors_changed(self) -> None:
        """Refresh the Auto Detectors menu after the controller mutated the detectors block."""
        self.menu_handler.update_auto_detectors_menu()

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
        """Filter events for window state changes + image-list Delete/Backspace."""
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMaximized:
                pass
            elif self.windowState() == Qt.WindowNoState:
                # When restored from maximised, set to 75% of the current screen.
                self.resize_to_percentage(0.75)
        if obj is self.image_list_widget and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
                self.remove_selected_images()
                return True
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
