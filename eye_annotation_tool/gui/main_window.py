"""Main application window for the eye annotation tool."""

import ast
from pathlib import Path

import numpy as np
import qtawesome as qta
from PyQt5.QtCore import QEvent, QRect, QSize, Qt, QTimer
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
from ..utils.project_settings import (
    CARRY_ROI_SLOTS,
    DETECTOR_TARGETS,
    PROJECT_FILE_SUFFIX,
    default_project,
    load_project,
    save_project,
)
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
        self._cli_monocular = bool(cli_monocular)
        self._cli_auto_detectors: set[str] | None = (
            set(cli_auto_detectors) if cli_auto_detectors is not None else None
        )
        # Session policy for CLI flag overrides. ``None`` = undecided
        # (no CLI flags or no project file loaded yet); ``True`` = user
        # accepted the override at startup, so CLI flags win for the
        # whole session; ``False`` = user kept the project settings,
        # CLI flags are ignored. Single source of truth queried by
        # :meth:`_monocular_locked` / :meth:`_auto_detectors_locked` —
        # adding a new CLI flag means extending one helper, not
        # scattering if/else checks across the code base.
        self._cli_overrides_active: bool | None = None
        self.binocular_mode = not self._cli_monocular
        # Project state: a single in-memory dict matching the on-disk
        # ``*.eye_annotation_project.json`` schema (see project_settings.py).
        # ``self.project_path`` is the absolute file path the project is
        # saved at, or ``None`` for an in-memory-only (unsaved) project.
        # When ``project_path`` is set, every state mutation that touches
        # the project (image add/remove, divider override change, autosave
        # toggle, ...) writes back to disk immediately.
        self.project: dict = default_project()
        self.project_path: str | None = None
        # Convenience accessor mirroring ``self.project["divider_x_norm"]``
        # so call sites that read the project-wide default can stay terse.
        # Per-image overrides live in ``self.project["images"][path]``.
        self.project_divider_x_norm = float(self.project["divider_x_norm"])
        # Per-eye snapshot of every plugin's last result on the current
        # image. The orchestrator only carries the active eye's results;
        # this dict carries the OTHER eye's so switching the radio can
        # restore that side without re-running. Only used in binocular
        # mode; the "single" key is used for monocular images.
        self._per_eye_detection_cache: dict[str, dict[Target, dict | None]] = {
            "left": dict.fromkeys(DETECTOR_TARGETS),
            "right": dict.fromkeys(DETECTOR_TARGETS),
            "single": dict.fromkeys(DETECTOR_TARGETS),
        }
        # Per-eye snapshot of every plugin's panel params (threshold,
        # ROI, gate values, ...). Switching the eye radio snapshots
        # the active eye's live panel state here, then restores the
        # other eye's state into the panel so each side carries its
        # own tuning. ``None`` means "no saved state yet — leave the
        # live panel as-is on restore".
        self._per_eye_panel_params: dict[str, dict[Target, dict | None]] = {
            "left": dict.fromkeys(DETECTOR_TARGETS),
            "right": dict.fromkeys(DETECTOR_TARGETS),
            "single": dict.fromkeys(DETECTOR_TARGETS),
        }
        # Carry-over ROI store. Both blocks are per-eye so toggling Carry
        # for one eye doesn't drag the other along. ``_carry_roi_enabled``
        # mirrors the active eye's checkbox state; ``_carry_roi_values``
        # holds the rectangle each (target, eye) carries forward. Both
        # are populated from the project file in
        # :meth:`_apply_enabled_plugins` and written back whenever the
        # checkbox flips or a canvas ROI edit lands while the active
        # eye's flag is on.
        self._carry_roi_enabled: dict[Target, dict[str, bool]] = {
            target: {"left": False, "right": False, "single": False} for target in DETECTOR_TARGETS
        }
        self._carry_roi_values: dict[Target, dict[str, tuple | None]] = {
            target: {"left": None, "right": None, "single": None} for target in DETECTOR_TARGETS
        }
        # Per-eye project defaults — populated once from the project file
        # in :meth:`_apply_enabled_plugins`. Used as the panel fallback
        # when the user switches to an eye that has no per-image override
        # or in-memory tuning yet.
        self._project_default_params: dict[Target, dict[str, dict | None]] = {
            target: {"left": None, "right": None, "single": None} for target in DETECTOR_TARGETS
        }
        self.autosave_enabled = False

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
        # Hashable identity of the last manual pupil ellipse we mirrored
        # into the orchestrator cache; lets on_annotation_changed cheaply
        # detect when the user has refitted the manual pupil so we can
        # republish the synthetic pupil result for dependent plugins.
        self._last_manual_pupil_signature: tuple | None = None

    @property
    def image_paths(self) -> list[str]:
        """Ordered list of image paths in the current project, derived from ``self.project``."""
        return list(self.project["images"].keys())

    def _persist_project(self) -> None:
        """Write ``self.project`` back to disk if the project is saved.

        No-op when ``self.project_path`` is ``None`` (unsaved in-memory
        project — the user must Save Project As before mutations persist).
        """
        if self.project_path is not None:
            save_project(self.project_path, self.project)

    def _project_divider_override(self, image_path: str) -> float | None:
        """Per-image divider override stored under ``project["images"][path]``."""
        entry = self.project["images"].get(image_path)
        if not isinstance(entry, dict):
            return None
        value = entry.get("divider_x_norm")
        return float(value) if isinstance(value, (int, float)) else None

    def _set_project_divider_override(self, image_path: str, value: float | None) -> None:
        """Set or clear the per-image divider override for ``image_path``."""
        entry = self.project["images"].setdefault(image_path, {})
        if value is None:
            entry.pop("divider_x_norm", None)
        else:
            entry["divider_x_norm"] = float(value)
        self._persist_project()

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
        self.load_images_button.clicked.connect(self.on_load_images_clicked)
        self.load_folder_button.clicked.connect(self.on_load_folder_clicked)
        self.prev_image_button.clicked.connect(self.navigation_controller.prev_image)
        self.next_image_button.clicked.connect(self.navigation_controller.next_image)
        self.save_annotations_button.clicked.connect(self.annotation_controller.save_annotations)
        self.remove_images_button.clicked.connect(self.remove_selected_images)
        self.image_list_widget.itemClicked.connect(self.navigation_controller.on_image_selected)
        self.image_list_widget.installEventFilter(self)

        self.annotation_controls.annotation_changed.connect(self.image_viewer.set_current_annotation)
        self.annotation_controls.eye_changed.connect(self._on_eye_changed)
        self.annotation_controls.binocular_toggled.connect(self._on_binocular_toggled)
        self.image_viewer.divider_x_norm_changed.connect(self._on_divider_x_norm_changed)
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

        self.image_viewer.target_roi_changed.connect(self._on_target_roi_changed)

        self._auto_detect_debounce.timeout.connect(self._on_auto_detect_debounce_fired)
        self.orchestrator.plugin_ready.connect(self._on_plugin_ready)
        self.orchestrator.plugin_failed.connect(self._on_plugin_failed)

    IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp")

    # ----- Project lifecycle (new / open / save / save-as) -------------

    def new_project(self, project_path: str, initial_project: dict | None = None) -> None:
        """Create a brand-new project file at ``project_path`` and load it.

        ``initial_project`` lets the New Project wizard pre-fill detector
        choices, binocular mode, etc.; missing keys fall back to
        :func:`default_project`. The file is written to disk immediately
        and becomes the active session project.
        """
        self.project = default_project()
        if isinstance(initial_project, dict):
            for key, value in initial_project.items():
                self.project[key] = value
        self.project_path = str(project_path)
        save_project(self.project_path, self.project)
        self._apply_project_state()
        self.current_image_index = 0 if self.image_paths else -1
        self.update_image_list()
        if self.image_paths:
            self.load_current_image()

    def open_project(self, project_path: str) -> None:
        """Load ``project_path`` from disk and apply it as the active project."""
        self.project = load_project(project_path)
        self.project_path = str(project_path)
        self._apply_project_state()
        self.current_image_index = 0 if self.image_paths else -1
        self.update_image_list()
        if self.image_paths:
            self.load_current_image()

    def save_project(self) -> None:
        """Write ``self.project`` to ``self.project_path``; prompt for path if unsaved."""
        if self.project_path is None:
            self.save_project_as()
            return
        save_project(self.project_path, self.project)

    def save_project_as(self) -> None:
        """Prompt for a save path and persist ``self.project`` there."""
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
        self.project_path = path
        save_project(self.project_path, self.project)

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
        for path in valid:
            self.project["images"].setdefault(path, {})
        self._persist_project()
        if not had_any_before:
            self.current_image_index = 0
        self.update_image_list()
        if self.image_paths and self.current_image_index < 0:
            self.current_image_index = 0
        self.load_current_image()

    def add_images_from_folder(self, folder: str) -> None:
        """Append every supported image directly inside ``folder`` (non-recursive)."""
        suffixes = self.IMAGE_SUFFIXES
        found = sorted(
            str(p) for p in Path(folder).iterdir()
            if p.is_file() and p.suffix.lower() in suffixes
        )
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
            if 0 <= self.current_image_index < len(self.image_paths) else None
        )
        for path in paths_to_remove:
            self.project["images"].pop(path, None)
        self._persist_project()
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
        """Push ``self.project`` settings into the dependent widgets.

        Called once after every project load (new / open). Mutating
        settings during a session is done in place on ``self.project``
        + ``self._persist_project()``; no need to call this method
        again unless the whole project is swapped.
        """
        self._resolve_cli_overrides_policy(self.project)
        self.project["binocular_mode"] = self._session_binocular(self.project.get("binocular_mode", True))
        self.project["detectors"] = self._session_detectors(self.project.get("detectors", {}))
        if self._cli_overrides_active:
            self._persist_project()
        self._apply_binocular_mode(bool(self.project.get("binocular_mode", True)))
        self.project_divider_x_norm = float(self.project.get("divider_x_norm", 0.5))
        self.image_viewer.set_divider_x_norm(self._effective_divider_x_norm())
        self._apply_enabled_plugins(self.project.get("detectors", {}))
        self.menu_handler.update_auto_detectors_menu()
        saved_mode = self.project.get("current_mode", MODE_MANUAL)
        if saved_mode == MODE_AUTO_DETECT:
            self.annotation_controls.mode_auto_detect_button.setChecked(True)
        else:
            self.annotation_controls.mode_manual_button.setChecked(True)
        autosave = bool(self.project.get("autosave", False))
        self.autosave_enabled = autosave
        self.autosave_checkbox.blockSignals(True)
        self.autosave_checkbox.setChecked(autosave)
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

    def _has_any_cli_override(self) -> bool:
        """Return True iff at least one CLI override flag was given."""
        return self._cli_monocular or self._cli_auto_detectors is not None

    def _monocular_locked(self) -> bool:
        """``--monocular`` is in force for this session."""
        return self._cli_monocular and self._cli_overrides_active is True

    def _auto_detectors_locked(self) -> bool:
        """``--auto-detectors`` is in force for this session."""
        return self._cli_auto_detectors is not None and self._cli_overrides_active is True

    def _session_binocular(self, source_value: bool) -> bool:
        """Apply the session policy to a raw binocular flag.

        Returns ``False`` when ``--monocular`` is locked for the session,
        otherwise returns ``source_value`` unchanged. Used by both the
        project-settings load path and the per-image meta load path so
        the policy gate lives in exactly one helper.
        """
        return False if self._monocular_locked() else bool(source_value)

    def _session_detectors(self, source_value: dict) -> dict:
        """Apply the session policy to a raw detectors dict.

        When ``--auto-detectors`` is locked, returns a dict with only
        the CLI-listed targets enabled (the others forced to
        ``"disabled"``); otherwise returns ``source_value`` unchanged.
        """
        if self._auto_detectors_locked():
            return self._override_detectors_from_cli(source_value)
        return source_value

    def _resolve_cli_overrides_policy(self, project_settings: dict) -> None:
        """Set ``self._cli_overrides_active`` once for the loaded project.

        With no CLI flags, the policy is irrelevant — set to ``False``
        and return. With CLI flags but no conflict (the project file
        already matches the requested state), set to ``True`` silently;
        applying overrides is a no-op so there is nothing for the user
        to decide. With CLI flags that disagree with the project file,
        show a dialog so the user picks which side wins for the session.
        """
        if not self._has_any_cli_override():
            self._cli_overrides_active = False
            return
        conflicts = self._cli_override_conflicts(project_settings)
        if not conflicts:
            self._cli_overrides_active = True
            return
        self._cli_overrides_active = self._ask_cli_overrides_dialog(conflicts)

    def _cli_override_conflicts(self, project_settings: dict) -> list[str]:
        """Return human-readable descriptions of fields where CLI disagrees with project."""
        conflicts: list[str] = []
        if self._cli_monocular and bool(project_settings.get("binocular_mode", True)):
            conflicts.append("Binocular mode: project file = binocular, CLI = monocular.")
        if self._cli_auto_detectors is not None:
            current = {
                target
                for target, block in project_settings.get("detectors", {}).items()
                if block.get("plugin", "disabled") != "disabled"
            }
            if current != self._cli_auto_detectors:
                conflicts.append(
                    "Auto detectors enabled: project file = "
                    f"{sorted(current) or 'none'}, CLI = {sorted(self._cli_auto_detectors)}.",
                )
        return conflicts

    def _ask_cli_overrides_dialog(self, conflicts: list[str]) -> bool:
        """Prompt: should the CLI flags override (and persist over) the project file?

        Yes -> CLI wins for the session AND the project settings file is
        rewritten with the CLI values (``_cli_overrides_active = True``).
        No  -> project file wins, CLI flags are ignored for the session.
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

    # ----- Mode / binocular toggles ------------------------------------

    def _apply_binocular_mode(self, enabled: bool) -> None:
        """Propagate the binocular flag to the dependent widgets."""
        self.binocular_mode = enabled
        self.image_viewer.set_binocular_mode(enabled)
        self.annotation_controls.set_binocular(enabled)

    def _on_binocular_toggled(self, enabled: bool) -> None:
        """Handle the Binocular checkbox flipping; persist to the project file."""
        self._apply_binocular_mode(enabled)
        self.project["binocular_mode"] = enabled
        self._persist_project()

    def _active_eye_slot(self) -> str:
        """Return the per-eye cache slot for the currently active eye.

        ``"left"`` / ``"right"`` in binocular mode, ``"single"`` in
        monocular mode. Used as the dict key for the per-eye detection
        cache and the per-eye JSON detection block.
        """
        if not self.binocular_mode:
            return "single"
        return self.image_viewer.current_eye

    def _on_eye_changed(self, eye: str) -> None:
        """Switch the active eye and swap the per-eye panel + orchestrator state.

        The viewer keeps each eye's detection overlay / mask / ROI in
        its own slot so both halves' work stays visible across the
        switch — only the panel state and the orchestrator's dep cache
        (which only ever holds the active eye) get swapped here. Live
        plugins re-run against the new eye so the active half's
        overlay tracks the new panel values without waiting for slider
        drags.
        """
        if not self.binocular_mode:
            return
        old_slot = self._active_eye_slot()
        self._snapshot_orchestrator_to_per_eye(old_slot)
        self._snapshot_panel_params_to_per_eye(old_slot)
        self.image_viewer.switch_eye(eye)
        new_slot = self._active_eye_slot()
        self._restore_panel_params_from_per_eye(new_slot)
        self._restore_orchestrator_from_per_eye(new_slot)
        self._refresh_carry_checkboxes()
        self._refresh_manual_pupil_in_cache()
        self._refresh_live_plugin_results()
        self._refresh_panel_availability()

    def _snapshot_orchestrator_to_per_eye(self, slot: str) -> None:
        """Copy the orchestrator's per-target results into the per-eye cache slot."""
        for target in DETECTOR_TARGETS:
            self._per_eye_detection_cache[slot][target] = self.orchestrator.cached_result(target)

    def _restore_orchestrator_from_per_eye(self, slot: str) -> None:
        """Push the per-eye cache slot's results back into the orchestrator."""
        for target in DETECTOR_TARGETS:
            self.orchestrator.set_cached_result(target, self._per_eye_detection_cache[slot][target])

    def _snapshot_panel_params_to_per_eye(self, slot: str) -> None:
        """Copy each live plugin panel's current params into the per-eye mirror."""
        for target, plugin in self._enabled_plugins.items():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            self._per_eye_panel_params[slot][target] = panel.current_params()

    def _restore_panel_params_from_per_eye(self, slot: str) -> None:
        """Push the per-eye mirror's saved params back into each plugin panel.

        Priority order per (target, slot): per-image mirror →
        project-file defaults → plugin's ``default_params``. The last
        fallback keeps the panel snapped to clean defaults on a slot
        the user has never tuned and that has no project default,
        rather than letting the previous eye's tuning leak across.
        """
        for target, plugin in self._enabled_plugins.items():
            params = self._per_eye_panel_params[slot].get(target)
            if params is None:
                params = self._project_default_params[target].get(slot)
            if params is None:
                params = plugin.default_params()
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is not None:
                panel.set_params(params)

    def _clear_per_eye_cache(self) -> None:
        """Wipe every per-eye cache slot (detection + panel params). Called on image change."""
        for slot in self._per_eye_detection_cache:
            for target in DETECTOR_TARGETS:
                self._per_eye_detection_cache[slot][target] = None
                self._per_eye_panel_params[slot][target] = None

    def _on_image_loaded(self) -> None:
        """Drop orchestrator cache + per-eye snapshots when a new image lands."""
        self.orchestrator.clear_cache()
        self._clear_per_eye_cache()

    def divider_override_for_current_image(self) -> float | None:
        """Return the per-image divider override for the current image (or ``None``)."""
        if not (0 <= self.current_image_index < len(self.image_paths)):
            return None
        return self._project_divider_override(self.image_paths[self.current_image_index])

    def _effective_divider_x_norm(self) -> float:
        """Return divider position for the current image (override or project default)."""
        override = self.divider_override_for_current_image()
        return self.project_divider_x_norm if override is None else override

    def apply_loaded_image_meta(self, *, binocular_mode: bool, divider_x_norm: float | None) -> None:
        """Apply binocular + divider metadata for a freshly loaded image.

        Called by AnnotationController right after the image's annotation
        JSON has been parsed. The per-image divider override (or ``None``
        to inherit the project default) is stashed for save round-trip,
        and the image viewer's divider position + binocular flag are
        updated so the canvas renders the correct geometry.

        The per-image binocular flag goes through :meth:`_session_binocular`
        so the active CLI override policy is the only gate — adding a
        new override flag does not require touching this method.
        """
        if 0 <= self.current_image_index < len(self.image_paths):
            self._set_project_divider_override(self.image_paths[self.current_image_index], divider_x_norm)
        effective_binocular = self._session_binocular(binocular_mode)
        if effective_binocular != self.binocular_mode:
            self._apply_binocular_mode(effective_binocular)
        self.image_viewer.set_divider_x_norm(self._effective_divider_x_norm())

    def _on_divider_x_norm_changed(self, value: float) -> None:
        """Persist a user-driven divider drag as a per-image override."""
        if not (0 <= self.current_image_index < len(self.image_paths)):
            return
        self._set_project_divider_override(self.image_paths[self.current_image_index], float(value))
        self.set_annotation_modified(True)

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
            self._cancel_active_roi_edit()
        # Manual click-to-place and click-to-edit on the canvas are
        # only allowed in Manual mode. Auto Detect mode still paints
        # manual annotations so the user can see them, but blocks edits
        # until they switch back.
        self.image_viewer.set_manual_edit_enabled(mode == MODE_MANUAL)
        self.project["current_mode"] = mode
        self._persist_project()

    def _cancel_active_roi_edit(self) -> None:
        """Drop the active ROI drag-edit state on the canvas and untoggle every panel button.

        Used when leaving Auto Detect mode so the canvas stops treating
        clicks as ROI edits and so the panel button doesn't stay stuck
        in its checked state when the user comes back.
        """
        self.image_viewer.set_active_roi_target(None)
        for plugin in self._enabled_plugins.values():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            button = getattr(panel, "roi_button", None) if panel is not None else None
            if button is not None and button.isChecked():
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)

    def update_image_list(self) -> None:
        """Update the image list widget with current image paths.

        When a project file is set, display each entry as its path relative
        to the project file's directory so files with the same basename in
        different subdirs are distinguishable. Falls back to bare filenames
        when the project hasn't been saved yet.
        """
        self.image_list_widget.clear()
        project_root = Path(self.project_path).parent if self.project_path else None
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
        """Handle annotation change event.

        Detects when the manual pupil ellipse identity changed and
        republishes a synthetic pupil result so downstream auto
        detectors (glint, limbus) pick up the updated centre / radius
        through their usual ``shared_results["pupil"]`` path. Live
        downstream plugins are re-run so their overlay tracks the new
        manual pupil immediately.
        """
        self.set_annotation_modified(True)
        if "pupil" in self._enabled_plugins:
            return
        new_sig = self._manual_pupil_signature()
        if new_sig == self._last_manual_pupil_signature:
            return
        self._last_manual_pupil_signature = new_sig
        self._refresh_manual_pupil_in_cache()
        self._refresh_live_plugin_results()
        self._refresh_panel_availability()

    def _on_autosave_changed(self, enabled: bool) -> None:
        """Persist the autosave toggle in project settings."""
        self.autosave_enabled = enabled
        self.project["autosave"] = enabled
        self._persist_project()
        self._refresh_save_state_indicator()

    def collect_detections_for_save(self) -> dict:
        """Walk every enabled plugin and build the per-image ``detections`` dict.

        Monocular images save flat: ``{plugin_name: {params, result}}``.
        Binocular images save nested per eye: ``{plugin_name: {left:
        {params, result}, right: {params, result}}}`` — each eye keeps
        its own params alongside its result so threshold / ROI / gate
        values restore independently on reload. The active eye's state
        is snapshotted into the mirror first so both eyes are read
        from a uniform source.
        """
        active_slot = self._active_eye_slot()
        self._snapshot_orchestrator_to_per_eye(active_slot)
        self._snapshot_panel_params_to_per_eye(active_slot)
        out: dict = {}
        for target, plugin in self._enabled_plugins.items():
            if self.binocular_mode:
                per_eye_block: dict = {}
                for slot in ("left", "right"):
                    result = self._per_eye_detection_cache[slot][target]
                    if result is None:
                        continue
                    params = self._per_eye_panel_params[slot].get(target) or plugin.default_params()
                    per_eye_block[slot] = {
                        "params": params,
                        "result": plugin.serialize(result),
                    }
                if per_eye_block:
                    out[plugin.name] = per_eye_block
            else:
                result = self._per_eye_detection_cache["single"][target]
                if result is None:
                    continue
                params = self._per_eye_panel_params["single"].get(target) or plugin.default_params()
                out[plugin.name] = {
                    "params": params,
                    "result": plugin.serialize(result),
                }
        return out

    def apply_loaded_detections(self, detections: dict) -> None:
        """Restore per-image detection blocks from a loaded annotation file.

        Two on-disk shapes are accepted: monocular files carry a flat
        ``{params, result}`` per plugin; binocular files carry a nested
        ``{left: {...}, right: {...}}`` per plugin. The per-eye cache
        is populated for both eyes from the binocular shape; the
        orchestrator's slot is then primed with the active eye's data
        so the canvas paints the right side on first frame.

        Blocks whose plugin is disabled or unknown for the current
        project are ignored. After the restore, every live cheap
        plugin is re-run once so the cache + mask are fresh — masks
        are stripped on serialise, so a loaded image otherwise has no
        mask data even when its Show-mask toggle is on. Non-live
        plugins keep their deserialised result until the user clicks
        Detect.
        """
        # Every per-eye setter below calls into ``ImageViewer`` and would
        # normally trigger a full canvas repaint. Pause repaints for the
        # duration of the restore so the dozens of overlay / ROI writes
        # collapse to a single paint at the end — the dominant fix for
        # image-navigation lag.
        self.image_viewer.pause_updates()
        try:
            active_slot = self._active_eye_slot()
            for plugin_name, blob in detections.items():
                plugin = self.plugin_manager.get(plugin_name)
                if plugin is None:
                    continue
                if self._enabled_plugins.get(plugin.target) is not plugin:
                    continue
                per_eye_params, per_eye_results = self._extract_loaded_plugin_blob(blob, plugin)
                # Restore each eye's params + result into the per-eye mirrors
                # and push each eye's overlay + ROI into the viewer under
                # its own slot so both halves' last results paint at once.
                for slot, params in per_eye_params.items():
                    if params is None:
                        continue
                    self._per_eye_panel_params[slot][plugin.target] = dict(params)
                    saved_roi = params.get(_panel_roi_param_key(plugin.target))
                    self.image_viewer.set_target_roi(
                        plugin.target,
                        tuple(saved_roi) if saved_roi is not None else None,
                        eye_slot=slot,
                    )
                for slot, result in per_eye_results.items():
                    self._per_eye_detection_cache[slot][plugin.target] = result
                    if result is not None:
                        self.image_viewer.set_detection_overlay(plugin.target, result, eye_slot=slot)
                active_params = per_eye_params.get(active_slot)
                active_result = per_eye_results.get(active_slot)
                panel = self.annotation_controls.auto_detect_panel(plugin.name)
                if panel is not None and active_params is not None:
                    panel.set_params(active_params)
                if active_result is not None:
                    self.orchestrator.set_cached_result(plugin.target, active_result)
            # Reset every enabled panel to the active eye's project
            # defaults (falling back to plugin defaults). Plugins whose
            # detection block was restored above keep their per-image
            # params because the per-eye mirror is populated; plugins
            # without per-image data snap back to project defaults
            # instead of inheriting the previous image's panel state.
            self._restore_panel_params_from_per_eye(active_slot)
            # Carry-over rectangles fill any (target, eye) slot the loaded
            # JSON didn't populate. Run after the JSON restore so saved
            # per-image ROIs always win.
            self._apply_carry_over_rois()
            self._refresh_carry_checkboxes()
            # Sync the manual-pupil mirror to the freshly loaded image's
            # ellipse before live plugins re-run so glint / limbus pick up
            # the right pupil source on first paint.
            self._last_manual_pupil_signature = self._manual_pupil_signature()
            self._refresh_manual_pupil_in_cache()
            self._refresh_live_plugins_all_eyes()
            self._refresh_panel_availability()
        finally:
            self.image_viewer.resume_updates()

    @staticmethod
    def _extract_loaded_plugin_blob(
        blob: dict,
        plugin: DetectorPlugin,
    ) -> tuple[dict[str, dict | None], dict[str, dict | None]]:
        """Normalise an on-disk plugin block to per-eye ``(params, results)`` maps.

        Returns ``(per_eye_params, per_eye_results)`` keyed by per-eye
        cache slot. For monocular files the single slot is
        ``"single"``; for binocular the slots are ``"left"`` /
        ``"right"`` (whichever are present in the blob). Each per-eye
        params dict may be ``None`` when the saved block carried no
        params for that eye.
        """
        # Monocular shape: flat {params, result}.
        if "params" in blob or "result" in blob:
            params = blob.get("params") or None
            result_blob = blob.get("result")
            result = plugin.deserialize(result_blob) if result_blob else None
            return {"single": params}, {"single": result}
        # Binocular shape: {left: {params, result}, right: {...}}.
        per_eye_params: dict[str, dict | None] = {}
        per_eye_results: dict[str, dict | None] = {}
        for slot in ("left", "right"):
            entry = blob.get(slot)
            if not isinstance(entry, dict):
                continue
            per_eye_params[slot] = entry.get("params") or None
            result_blob = entry.get("result")
            per_eye_results[slot] = plugin.deserialize(result_blob) if result_blob else None
        return per_eye_params, per_eye_results

    def _manual_pupil_signature(self) -> tuple | None:
        """Return a hashable identity of the current eye's manual pupil ellipse."""
        pupil_ellipse = self.image_viewer.pupil_ellipse
        if pupil_ellipse is None:
            return None
        center, size, angle = pupil_ellipse
        return (center.x(), center.y(), size.width(), size.height(), angle)

    def _build_synthetic_pupil_from_manual(self) -> dict | None:
        """Build a pupil-plugin-shaped result from the current manual pupil ellipse, or None."""
        pupil_ellipse = self.image_viewer.pupil_ellipse
        if pupil_ellipse is None:
            return None
        center, size, angle = pupil_ellipse
        cx, cy = float(center.x()), float(center.y())
        return {
            "center": [cx, cy],
            "ellipse": {
                "center": [cx, cy],
                "size": [float(size.width()), float(size.height())],
                "angle": float(angle),
            },
        }

    def _refresh_manual_pupil_in_cache(self) -> None:
        """Mirror the current manual pupil ellipse into the orchestrator cache.

        Lets glint / limbus auto detectors consume a manually fitted pupil
        through the same ``shared_results["pupil"]`` path they use for an
        auto pupil result. No-op when an auto pupil plugin is enabled —
        that plugin owns the cache slot.
        """
        if "pupil" in self._enabled_plugins:
            return
        synthetic = self._build_synthetic_pupil_from_manual()
        self.orchestrator.set_cached_result("pupil", synthetic)

    def _refresh_panel_availability(self) -> None:
        """Disable each Auto Detect panel whose ``requires`` are unmet.

        A dep is met when the orchestrator carries a non-None cached
        result for it (either from a successful auto plugin run or from
        the manual-pupil synthetic). Disabled panels grey out their
        controls so the user sees that the upstream target needs to be
        provided first.
        """
        for plugin in self._enabled_plugins.values():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            deps_met = all(self.orchestrator.cached_result(dep) is not None for dep in plugin.requires)
            panel.setEnabled(deps_met)

    def _refresh_live_plugin_results(self) -> None:
        """Re-run every enabled live plugin on the active eye, in dep order.

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
            self._run_plugin_for_active_eye(plugin, panel.current_params())

    def _refresh_live_plugins_all_eyes(self) -> None:
        """Run live plugins for the active eye only.

        The non-active eye is intentionally skipped — programmatically
        running live plugins on a slot the user never visited would
        populate that slot's detection cache with default-params output
        and then autosave would persist those defaults to disk, looking
        like the user tuned that eye when they hadn't. Switching the
        active eye (via the radio) triggers the live run for the new
        side, so both eyes are still covered with one user click each.
        """
        self._refresh_live_plugin_results()

    # ----- Binocular crop + translate (active-eye-aware run path) -----

    def _active_eye_crop_bounds(self) -> tuple[int, int, int, int] | None:
        """Return ``(dx, dy, dw, dh)`` for the active eye's half, or ``None`` (no crop)."""
        if not self.binocular_mode:
            return None
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return None
        full_h, full_w = image.shape[:2]
        divider_x = round(self._effective_divider_x_norm() * full_w)
        divider_x = max(1, min(full_w - 1, divider_x))
        if self.image_viewer.current_eye == "left":
            return (0, 0, divider_x, full_h)
        return (divider_x, 0, full_w - divider_x, full_h)

    @staticmethod
    def _intersect_roi_with_crop(
        roi: tuple | None,
        crop: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int] | None:
        """Translate a full-image ROI into crop coords, or return None if no overlap."""
        if roi is None:
            return None
        rx, ry, rw, rh = roi
        cx, cy, cw, ch = crop
        ix = max(rx, cx)
        iy = max(ry, cy)
        ex = min(rx + rw, cx + cw)
        ey = min(ry + rh, cy + ch)
        iw = ex - ix
        ih = ey - iy
        if iw <= 0 or ih <= 0:
            return None
        return (int(ix - cx), int(iy - cy), int(iw), int(ih))

    @staticmethod
    def _embed_mask(mask: np.ndarray, dx: int, dy: int, full_shape: tuple) -> np.ndarray:
        """Paste a crop-sized mask into a full-image-sized zeros array at ``(dx, dy)``."""
        full_h, full_w = full_shape[:2]
        embedded = np.zeros((full_h, full_w), dtype=mask.dtype)
        mh, mw = mask.shape[:2]
        embedded[dy : dy + mh, dx : dx + mw] = mask
        return embedded

    def _run_plugin_for_active_eye(self, plugin: DetectorPlugin, params: dict) -> None:
        """Dispatch a plugin run, cropping to the active eye half when relevant.

        Pupil plugins running in binocular mode get a cropped grayscale
        and a translated ``pupil_roi`` param; the result is translated
        back to full-image coordinates via ``plugin.translate_for_crop``
        and any returned mask is embedded into a full-image-sized array
        before it reaches the viewer. All non-pupil plugins (or any
        plugin in monocular mode) run on the full image as before —
        glint + limbus consume the already-full-coord pupil result via
        ``shared_results`` and their search regions naturally stay
        within the active eye half because pupil_center does.
        """
        image = self.image_viewer.get_current_image_grayscale()
        if image is None:
            return
        bounds = self._active_eye_crop_bounds() if plugin.target == "pupil" else None
        if bounds is None:
            self.orchestrator.run_one(plugin.target, image, params)
            return
        dx, dy, dw, dh = bounds
        cropped = image[dy : dy + dh, dx : dx + dw]
        translated_params = dict(params)
        roi_key = _panel_roi_param_key(plugin.target)
        if roi_key in translated_params:
            translated_params[roi_key] = self._intersect_roi_with_crop(translated_params.get(roi_key), bounds)
        full_shape = image.shape

        def post_process(result: dict) -> dict:
            translated = plugin.translate_for_crop(result, dx, dy)
            mask = translated.get("mask")
            if mask is not None:
                translated["mask"] = self._embed_mask(mask, dx, dy, full_shape)
            return translated

        self.orchestrator.run_one(plugin.target, cropped, translated_params, post_process=post_process)

    # ----- Auto Detectors menu: per-target plugin choice + project defaults -----

    def current_plugin_for_target(self, target: Target) -> str:
        """Return the slug of the plugin currently chosen for ``target`` (or ``"disabled"``)."""
        return self.project.get("detectors", {}).get(target, {}).get("plugin", "disabled")

    def select_plugin_for_target(self, target: Target, plugin_name: str) -> None:
        """Set ``target``'s plugin to ``plugin_name`` and rebuild the Auto Detect panels.

        ``plugin_name`` is either an existing plugin slug or the literal
        ``"disabled"``. Switching plugins resets the project-saved
        params for that target to the new plugin's
        :meth:`~DetectorPlugin.default_params` so old slider values for
        a different plugin do not leak across. Switching to the same
        plugin is a no-op.
        """
        detectors = self.project.setdefault("detectors", {})
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
        self._persist_project()
        # Wipe per-image overlay / ROI / mask state across both eyes
        # for the target the user is actually changing — the old
        # plugin's result must not leak into the new plugin's slot.
        self.image_viewer.clear_detection_overlay(target)
        self.image_viewer.clear_target_roi(target)
        self.image_viewer.clear_target_mask(target)
        # Enabling a detector takes the target away from the manual side;
        # wipe any previously placed manual annotations for it so each
        # target has a single source of truth.
        if plugin_name != "disabled":
            self.image_viewer.clear_manual_for_target(target)
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
        # slider. Both eyes run in binocular mode.
        self._refresh_live_plugins_all_eyes()

    def save_current_settings_as_project_defaults(self) -> None:
        """Snapshot every enabled plugin's current panel params into project defaults.

        A confirmation dialog guards the action — the project file is
        about to be overwritten with the slider state from the Auto
        Detect panels.
        """
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
        active_slot = self._active_eye_slot()
        # Snapshot the active eye's live panel state into the per-eye mirror
        # so the project file captures both eyes at once when the user has
        # been tuning per-eye in the same image.
        self._snapshot_panel_params_to_per_eye(active_slot)
        detectors = self.project.setdefault("detectors", {})
        for target, plugin in self._enabled_plugins.items():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            viewer_rois = {slot: self.image_viewer.get_target_roi(target, eye_slot=slot) for slot in CARRY_ROI_SLOTS}
            for slot, roi in viewer_rois.items():
                self._carry_roi_values[target][slot] = roi
            # Per-eye params: take each slot's working state from the
            # per-eye mirror; slots the user has never tuned stay null.
            # The ROI lives in carry_roi.values, so strip the duplicate
            # ``<target>_roi`` slot from each per-eye params dict.
            params_by_slot: dict[str, dict | None] = {}
            for slot in CARRY_ROI_SLOTS:
                per_slot = self._per_eye_panel_params[slot].get(target)
                if per_slot is None:
                    params_by_slot[slot] = None
                    continue
                cleaned = dict(per_slot)
                cleaned.pop(_panel_roi_param_key(target), None)
                params_by_slot[slot] = cleaned
                self._project_default_params[target][slot] = dict(cleaned)
            detectors[target] = {
                "plugin": plugin.name,
                "params": params_by_slot,
                "carry_roi": {
                    "enabled": {
                        slot: bool(self._carry_roi_enabled[target][slot]) for slot in CARRY_ROI_SLOTS
                    },
                    "values": {slot: list(roi) if roi is not None else None for slot, roi in viewer_rois.items()},
                },
            }
        self._persist_project()
        self._refresh_carry_checkboxes()
        self.statusBar().showMessage("Project defaults saved.", 3000)

    # ----- Auto Detect mode: plugin resolution, run dispatch, signal forwarding -----

    def _override_detectors_from_cli(self, project_detectors: dict) -> dict:
        """Return a detectors dict honouring ``--auto-detectors`` for this session.

        Targets in ``self._cli_auto_detectors`` keep the project file's
        plugin choice (and any tuned params) untouched; every other
        target is forced to ``"disabled"``. The original
        ``project_detectors`` dict is not mutated.

        Fails fast when a CLI-enabled target is set to ``"disabled"`` in
        the project file — that combination is a user-side conflict and
        silently substituting a default plugin would surprise the user.
        """
        overridden: dict = {}
        for target in DETECTOR_TARGETS:
            existing = project_detectors.get(target, {})
            if target in self._cli_auto_detectors:
                plugin_slug = existing.get("plugin", "disabled")
                if plugin_slug == "disabled":
                    raise SystemExit(
                        f"--auto-detectors includes {target!r} but the project "
                        f"settings file has {target!r} set to 'disabled'. Enable "
                        f"a plugin for {target!r} via the Auto Detectors menu "
                        f"(or hand-edit the settings file) and re-run, or drop "
                        f"{target!r} from --auto-detectors.",
                    )
                overridden[target] = {"plugin": plugin_slug, "params": dict(existing.get("params", {}))}
            else:
                overridden[target] = {"plugin": "disabled", "params": {}}
        return overridden

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
                # Drop any previously registered plugin so the viewer
                # stops trying to draw / colour overlays for this target.
                self.image_viewer.clear_active_plugin(target)
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
            # Tell the viewer which plugin owns this target so it can
            # call ``plugin.draw_overlay`` and pick up the plugin's
            # ``roi_color`` / ``mask_color`` palette when rendering.
            self.image_viewer.set_active_plugin(target, plugin)
            panel = plugin.make_panel(self)
            # Cache the per-eye project defaults so eye-switch can fall
            # back to them when the active slot has no in-memory tuning
            # or per-image override.
            params_by_slot = entry.get("params") or {}
            for slot in CARRY_ROI_SLOTS:
                slot_params = params_by_slot.get(slot) if isinstance(params_by_slot, dict) else None
                self._project_default_params[target][slot] = (
                    dict(slot_params) if isinstance(slot_params, dict) else None
                )
            active_slot = self._active_eye_slot()
            initial_params = (
                preserved_params.get(target)
                or self._project_default_params[target].get(active_slot)
                or {}
            )
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
            # Restore the per-eye carry-over enable flags + rectangle values
            # from project settings. The checkbox state lives on the panel
            # and tracks the active eye; the rectangle values live on
            # MainWindow and only apply to subsequent image loads.
            carry_block = entry.get("carry_roi") or {}
            enabled_by_slot_in = carry_block.get("enabled")
            if not isinstance(enabled_by_slot_in, dict):
                enabled_by_slot_in = {}
            for slot in CARRY_ROI_SLOTS:
                self._carry_roi_enabled[target][slot] = bool(enabled_by_slot_in.get(slot, False))
            carry_values = carry_block.get("values") or {}
            for slot in CARRY_ROI_SLOTS:
                value = carry_values.get(slot)
                self._carry_roi_values[target][slot] = (
                    tuple(int(c) for c in value) if isinstance(value, (list, tuple)) and len(value) == 4 else None
                )
            if hasattr(panel, "set_carry_roi_enabled"):
                panel.set_carry_roi_enabled(self._carry_roi_enabled[target][active_slot])
            if hasattr(panel, "carry_roi_toggled"):
                panel.carry_roi_toggled.connect(
                    lambda checked, target_=plugin.target: self._on_carry_roi_toggled(target_, checked),
                )
            if hasattr(panel, "override_roi_requested"):
                panel.override_roi_requested.connect(
                    lambda target_=plugin.target: self._on_override_roi_requested(target_),
                )
            panels.append((plugin.name, panel))
        self.annotation_controls.set_auto_detect_panels(panels)
        self.orchestrator.set_enabled_plugins(dict(self._enabled_plugins))
        self._sync_manual_for_auto_targets()
        # Re-publish the manual pupil synthetic in case pupil ownership
        # just flipped from auto back to manual, and gate the freshly
        # mounted panels by their dependency availability.
        self._last_manual_pupil_signature = self._manual_pupil_signature()
        self._refresh_manual_pupil_in_cache()
        self._refresh_panel_availability()

    def _sync_manual_for_auto_targets(self) -> None:
        """Mirror per-target detector ownership into the Manual panel + viewer.

        For each target with an enabled auto detector the matching
        Manual-panel row is hidden entirely (rather than greyed in
        place) — leaving a disabled row dangling looked broken. The
        viewer is told to suppress that target's manual painting and
        click-add. If the currently selected Manual row just got
        hidden, selection jumps to the first still-visible row so
        canvas clicks don't fall through to an invisible target.
        """
        target_rows = (
            ("pupil", self.annotation_controls.pupil_group, "pupil"),
            ("limbus", self.annotation_controls.limbus_group, "limbus"),
            ("eyelid", self.annotation_controls.eyelid_group, "eyelid_contour"),
            ("glint", self.annotation_controls.glint_group, "glint"),
        )
        auto_targets: set[str] = set()
        first_visible: tuple[object, str] | None = None
        selected_hidden = False
        for plugin_target, group, annotation in target_rows:
            has_auto = plugin_target in self._enabled_plugins
            group.setVisible(not has_auto)
            if has_auto:
                auto_targets.add(plugin_target)
                if group.is_checked():
                    selected_hidden = True
            elif first_visible is None:
                first_visible = (group, annotation)
        self.image_viewer.set_auto_managed_targets(auto_targets)
        if selected_hidden and first_visible is not None:
            group, annotation = first_visible
            group.set_checked(True)
            self.image_viewer.set_current_annotation(annotation)

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
        active_slot = self._active_eye_slot()
        self.orchestrator.set_cached_result(target, None)
        self._per_eye_detection_cache[active_slot][target] = None
        self.image_viewer.clear_detection_overlay(target, eye_slot=active_slot)
        self.image_viewer.clear_target_mask(target, eye_slot=active_slot)

    def _on_auto_detect_debounce_fired(self) -> None:
        """Dispatch the buffered run to the active-eye-aware run helper.

        Skipped silently when no image grayscale is available (no image
        loaded yet) or the plugin was disabled between buffering and firing.
        """
        pending = self._pending_run_one
        self._pending_run_one = None
        if pending is None:
            return
        plugin_name, params = pending
        plugin = self.plugin_manager.get(plugin_name)
        if plugin is None or self._enabled_plugins.get(plugin.target) is not plugin:
            return
        self._run_plugin_for_active_eye(plugin, params)

    def _on_plugin_ready(self, target: str, result: dict) -> None:
        """Render the new detection result for the active eye + mark modified.

        The mask, overlay and per-eye cache slot are scoped to the
        active eye — the inactive eye keeps its previously stored
        result so its half of the canvas keeps painting.
        """
        active_slot = self._active_eye_slot()
        # The mask (if any) lives under the standard ``"mask"`` key per
        # the plugin contract — split it off from the geometry overlay so
        # the viewer's mask renderer and its detection renderer stay on
        # their own paths.
        self.image_viewer.set_target_mask(target, result.get("mask"), eye_slot=active_slot)
        self.image_viewer.set_detection_overlay(target, result, eye_slot=active_slot)
        self._per_eye_detection_cache[active_slot][target] = result
        self.set_annotation_modified(True)
        self._refresh_panel_availability()

    def _on_plugin_failed(self, target: str) -> None:
        """Clear the active eye's overlay + mask for ``target`` and report in the status bar."""
        active_slot = self._active_eye_slot()
        self.image_viewer.clear_detection_overlay(target, eye_slot=active_slot)
        self.image_viewer.clear_target_mask(target, eye_slot=active_slot)
        self._per_eye_detection_cache[active_slot][target] = None
        self.statusBar().showMessage(f"Auto Detect: {target} failed at current parameters.", 5000)
        self._refresh_panel_availability()

    def _on_clear_all(self) -> None:
        """Wipe every manual annotation AND every Auto Detect result on the current image.

        Clear All is mode-agnostic by design: it drops the manual
        point/ellipse sets across both eyes and resets every mounted
        plugin panel + cached detection + overlay + mask + ROI. No
        detection re-runs after the clear.
        """
        self.image_viewer.clear_all()
        self._clear_all_auto_detect()

    def _clear_all_auto_detect(self) -> None:
        """Reset every Auto Detect plugin panel + orchestrator + per-eye cache + viewer state."""
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
            self.image_viewer.clear_target_roi(target)
        self._clear_per_eye_cache()
        # Active drag-edit target was likely tied to one of the panels we
        # just reset; drop it so the canvas isn't waiting on a phantom drag.
        self.image_viewer.set_active_roi_target(None)
        self.set_annotation_modified(True)

    def _on_panel_detect_requested(self, plugin_name: str, target: Target) -> None:
        """Run a non-live plugin once on the active eye.

        Wired to the plugin panel's Detect button. The plugin runs via
        the active-eye-aware helper so binocular images detect on the
        currently selected half. The orchestrator emits
        ``plugin_ready`` (or ``plugin_failed``) which lands the new
        overlay via the usual ``_on_plugin_ready`` path.
        """
        plugin = self._enabled_plugins.get(target)
        if plugin is None or plugin.name != plugin_name:
            return
        panel = self.annotation_controls.auto_detect_panel(plugin_name)
        if panel is None:
            return
        self._run_plugin_for_active_eye(plugin, panel.current_params())

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
        """Push a canvas-edited ROI back into the panel; disable Carry on edit.

        The panel's setter (e.g. ``set_pupil_roi``) writes the new value
        into the params dict and emits ``params_changed``; the regular
        debounce path then re-runs detection with the updated ROI.

        A canvas edit means the user is tuning THIS image's ROI
        specifically, so Carry auto-disables for the active eye when a
        non-empty rectangle lands. The other eye's carry stays untouched —
        each eye's Carry flag is independent.
        """
        plugin = self._enabled_plugins.get(target)
        if plugin is None:
            return
        panel = self.annotation_controls.auto_detect_panel(plugin.name)
        setter = getattr(panel, _panel_roi_setter_name(target), None) if panel is not None else None
        if setter is not None:
            setter(roi)
        active_slot = self._active_eye_slot()
        if roi is not None and self._carry_roi_enabled.get(target, {}).get(active_slot, False):
            self._carry_roi_enabled[target][active_slot] = False
            if panel is not None and hasattr(panel, "set_carry_roi_enabled"):
                panel.set_carry_roi_enabled(False)
            self._persist_carry_roi(target)

    def _on_carry_roi_toggled(self, target: Target, enabled: bool) -> None:
        """Persist the active eye's carry-over enable flag for ``target``.

        The Carry checkbox is per-eye: flipping the flag on captures the
        *current* canvas ROI for the active eye as the initial carry-over
        value so the next image load already has something to apply.
        Turning it off leaves the stored rectangle in place — re-enabling
        later resumes from the same value. The other eye's Carry flag is
        untouched.
        """
        active_slot = self._active_eye_slot()
        self._carry_roi_enabled[target][active_slot] = bool(enabled)
        if enabled:
            current_roi = self.image_viewer.get_target_roi(target)
            if current_roi is not None:
                self._carry_roi_values[target][active_slot] = tuple(int(c) for c in current_roi)
        self._persist_carry_roi(target)
        self._refresh_carry_checkboxes()

    def _on_override_roi_requested(self, target: Target) -> None:
        """Push the stored carry-over rectangle into the active eye's panel + viewer.

        Replaces whatever ROI this image had for that target — saved or
        not — with the carry-over value. The panel's setter emits
        ``params_changed`` so a live plugin re-runs immediately. No-op
        when no carry value is stored for the active eye.
        """
        active_slot = self._active_eye_slot()
        carry_value = self._carry_roi_values.get(target, {}).get(active_slot)
        if carry_value is None:
            return
        plugin = self._enabled_plugins.get(target)
        if plugin is None:
            return
        panel = self.annotation_controls.auto_detect_panel(plugin.name)
        setter = getattr(panel, _panel_roi_setter_name(target), None) if panel is not None else None
        if setter is not None:
            setter(tuple(carry_value))
        self.image_viewer.set_target_roi(target, tuple(carry_value), eye_slot=active_slot)
        self._refresh_carry_checkboxes()

    def _persist_carry_roi(self, target: Target) -> None:
        """Write the per-eye carry-over enable flags + values for ``target`` to the project file."""
        detectors = self.project.setdefault("detectors", {})
        entry = detectors.setdefault(target, {"plugin": "disabled", "params": {}})
        entry["carry_roi"] = {
            "enabled": {
                slot: bool(self._carry_roi_enabled.get(target, {}).get(slot, False)) for slot in CARRY_ROI_SLOTS
            },
            "values": {
                slot: list(value) if value is not None else None
                for slot, value in self._carry_roi_values[target].items()
            },
        }
        self._persist_project()

    def _refresh_carry_checkboxes(self) -> None:
        """Sync each panel's Carry checkbox + Override button to the active eye's state.

        A panel's checkbox shows as **checked** only when the carry-over
        is enabled for that target AND the active eye's current viewer
        ROI matches the stored carry value bit-for-bit. So loading an
        image whose saved ROI differs from the carry-over leaves the
        checkbox unchecked — a visual cue that "this image isn't the
        one we're propagating".

        The Override button is enabled only when a carry value is
        stored for the active (target, eye) — clicking it pushes that
        value into the canvas regardless of any saved ROI on the
        current image.
        """
        active_slot = self._active_eye_slot()
        for target, plugin in self._enabled_plugins.items():
            panel = self.annotation_controls.auto_detect_panel(plugin.name)
            if panel is None:
                continue
            if hasattr(panel, "set_carry_roi_enabled"):
                panel.set_carry_roi_enabled(self._carry_checkbox_state(target, active_slot))
            if hasattr(panel, "set_override_button_enabled"):
                has_value = self._carry_roi_values.get(target, {}).get(active_slot) is not None
                panel.set_override_button_enabled(has_value)

    def _carry_checkbox_state(self, target: Target, slot: str) -> bool:
        """Return whether the Carry checkbox should display as checked for ``(target, slot)``."""
        if not self._carry_roi_enabled.get(target, {}).get(slot, False):
            return False
        carry_value = self._carry_roi_values.get(target, {}).get(slot)
        if carry_value is None:
            return False
        viewer_roi = self.image_viewer.get_target_roi(target, eye_slot=slot)
        if viewer_roi is None:
            return False
        return tuple(int(c) for c in viewer_roi) == tuple(int(c) for c in carry_value)

    def _apply_carry_over_rois(self) -> None:
        """Inject the carry-over rectangle into every (target, eye) without a saved ROI.

        Called at the tail of :meth:`apply_loaded_detections` so saved
        per-image ROIs take precedence — the carry-over only fills the
        slots that the JSON didn't populate. The viewer's per-eye ROI
        store and the active eye's live panel are both updated.
        """
        active_slot = self._active_eye_slot()
        slots = ("left", "right") if self.binocular_mode else ("single",)
        for target, plugin in self._enabled_plugins.items():
            enabled_by_slot = self._carry_roi_enabled.get(target, {})
            roi_key = _panel_roi_param_key(target)
            for slot in slots:
                if not enabled_by_slot.get(slot, False):
                    continue
                if self._per_eye_panel_params[slot].get(target) is not None:
                    params = self._per_eye_panel_params[slot][target]
                    if params.get(roi_key) is not None:
                        continue
                else:
                    params = None
                carry_value = self._carry_roi_values[target].get(slot)
                if carry_value is None:
                    continue
                if params is None:
                    self._per_eye_panel_params[slot][target] = {roi_key: list(carry_value)}
                else:
                    params[roi_key] = list(carry_value)
                self.image_viewer.set_target_roi(target, tuple(carry_value), eye_slot=slot)
                if slot == active_slot:
                    panel = self.annotation_controls.auto_detect_panel(plugin.name)
                    panel_setter = getattr(panel, _panel_roi_setter_name(target), None) if panel is not None else None
                    if panel_setter is not None:
                        panel_setter(tuple(carry_value))

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
