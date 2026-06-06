"""Main application window for the eye annotation tool."""

import math
from pathlib import Path

import qtawesome as qta
from PyQt5.QtCore import QEvent, QRect, QSize, Qt
from PyQt5.QtGui import QCloseEvent, QIcon, QScreen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSlider,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from eye_annotation_tool.auto_detectors.plugin_loader import discover_plugins

from ..auto_detectors.orchestrator import DetectorOrchestrator
from ..controllers.annotation_controller import AnnotationController
from ..controllers.binocular_controller import BinocularController
from ..controllers.detection_controller import DetectionController
from ..controllers.navigation_controller import NavigationController
from ..policy import CliOverridePolicy
from ..state import CarryRoiStore, PerEyeStateStore, ProjectStore, SessionState
from ..utils.project_settings import (
    KINDS,
    PROJECT_FILE_SUFFIX,
    ProjectSchemaError,
)
from .about_dialog import show_about_dialog
from .annotation_controls import AnnotationControlPanel
from .custom_widgets import MaterialButton
from .image_tree import ImageTree
from .image_viewer import ImageViewer
from .menu_handler import MenuHandler
from .new_project_dialog import NewProjectDialog
from .project_settings_dialog import ProjectSettingsDialog
from .shortcut_handler import ShortcutHandler

# Slider ticks map onto the zoom / brightness controller's clamp range via a
# log scale (multiplicative feel — equal slider steps multiply the factor).
_ZOOM_SLIDER_MIN = 0
_ZOOM_SLIDER_MAX = 1000
_ZOOM_SLIDER_DEFAULT = 400  # ~1.0x (corresponds to slider position for factor 1)
_ZOOM_MIN_FACTOR = 0.1
_ZOOM_MAX_FACTOR = 25.0

_BRIGHTNESS_SLIDER_MIN = 0
_BRIGHTNESS_SLIDER_MAX = 1000
_BRIGHTNESS_SLIDER_DEFAULT = 500  # 1.0x — identity brightness
_BRIGHTNESS_MIN_FACTOR = 0.1
_BRIGHTNESS_MAX_FACTOR = 10.0


def _log_slider_to_factor(value: int, slider_min: int, slider_max: int, min_factor: float, max_factor: float) -> float:
    """Map an integer slider position to a log-scaled multiplicative factor."""
    span = slider_max - slider_min
    t = (value - slider_min) / span if span else 0
    log_lo, log_hi = math.log(min_factor), math.log(max_factor)
    return math.exp(log_lo + t * (log_hi - log_lo))


def _factor_to_log_slider(
    factor: float, slider_min: int, slider_max: int, min_factor: float, max_factor: float
) -> int:
    """Inverse of :func:`_log_slider_to_factor`."""
    log_lo, log_hi = math.log(min_factor), math.log(max_factor)
    t = (math.log(max(min_factor, min(max_factor, factor))) - log_lo) / (log_hi - log_lo)
    return round(slider_min + t * (slider_max - slider_min))


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
                subset of ``KINDS`` for the whole session;
                kinds not in the set are forced to ``"disabled"``
                regardless of the per-project detector choices. ``None``
                (the default) defers to the project file.

        """
        super().__init__()
        self.setWindowTitle("EyE Annotation Tool")
        self.orchestrator = DetectorOrchestrator(self)
        self.cli_policy = CliOverridePolicy(cli_monocular, cli_auto_detectors)
        self.project_store = ProjectStore()
        self.per_eye_state = PerEyeStateStore(KINDS)
        self.carry_roi_state = CarryRoiStore(KINDS)
        self.session = SessionState(self)
        self.session.modified_changed.connect(self._refresh_save_state_indicator)

        # Group cheshm detectors by kind so the side-panel cards have
        # the right options in their dropdowns.
        self._detectors_by_kind: dict[str, list] = {t: [] for t in KINDS}
        for det in discover_plugins():
            self._detectors_by_kind.setdefault(det.kind, []).append(det)

        self.setup_ui()

        self.detection_controller = DetectionController(
            self.orchestrator,
            self.per_eye_state,
            self.carry_roi_state,
            self.project_store,
            self.image_viewer,
            self.annotation_controls,
            parent=self,
        )
        self.detection_controller.annotation_modified.connect(self._mark_modified)
        self.detection_controller.status_message.connect(self.statusBar().showMessage)
        self.image_viewer.set_overlay_state_lookup(self.detection_controller.overlay_state_lookup)

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
        self.detection_controller.bind_binocular_controller(self.binocular_controller)
        self.binocular_controller.apply_mode(not self.cli_policy.monocular)
        self.binocular_controller.annotation_modified.connect(self._mark_modified)

        self.annotation_controller = AnnotationController(
            self.image_viewer,
            self.detection_controller,
            self.binocular_controller,
            self.project_store,
            self.session,
            dialog_parent=self,
        )
        self.navigation_controller = NavigationController(
            self.annotation_controller,
            self.project_store,
            self.session,
            self.image_tree,
            self.load_current_image,
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
        self.image_viewer = ImageViewer()
        left_panel = self._build_left_panel()
        right_panel = self._build_right_panel()

        central_widget = QWidget()
        main_layout = QHBoxLayout()
        main_layout.addWidget(left_panel)
        main_layout.addWidget(self.image_viewer, 1)
        main_layout.addWidget(right_panel)
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        self.setStatusBar(QStatusBar())
        self.image_viewer.setFocus()

    def _build_left_panel(self) -> QWidget:
        """Build the left panel: load/navigate/save buttons, image list, zoom + brightness rows."""
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        self.load_images_button = MaterialButton("Load Images")
        self.load_folder_button = MaterialButton("Load Images from Folder")
        self.recursive_folder_checkbox = QCheckBox("Include subfolders")
        self.recursive_folder_checkbox.setChecked(True)
        self.recursive_folder_checkbox.setToolTip("When loading a folder, also add images from all of its subfolders.")
        self.prev_image_button = MaterialButton("Previous Image")
        self.next_image_button = MaterialButton("Next Image")
        self.save_annotations_button = MaterialButton("Save Annotations")
        self.autosave_checkbox = QCheckBox("Autosave on image change")
        self.autosave_checkbox.toggled.connect(self._on_autosave_changed)

        left_layout.addWidget(self.load_images_button)
        left_layout.addWidget(self.load_folder_button)
        left_layout.addWidget(self.recursive_folder_checkbox)
        left_layout.addWidget(self.prev_image_button)
        left_layout.addWidget(self.next_image_button)
        left_layout.addWidget(self.save_annotations_button)
        left_layout.addWidget(self.autosave_checkbox)

        # Image list header: title plus expand-all / collapse-all controls.
        tree_header = QHBoxLayout()
        tree_header.setContentsMargins(0, 0, 0, 0)
        tree_header.addWidget(QLabel("Loaded Images:"))
        tree_header.addStretch(1)
        self.expand_all_button = QToolButton()
        self.expand_all_button.setIcon(qta.icon("mdi6.expand-all", color="#e0e0e0"))
        self.expand_all_button.setAutoRaise(True)
        self.expand_all_button.setToolTip("Expand all folders")
        self.collapse_all_button = QToolButton()
        self.collapse_all_button.setIcon(qta.icon("mdi6.collapse-all", color="#e0e0e0"))
        self.collapse_all_button.setAutoRaise(True)
        self.collapse_all_button.setToolTip("Collapse all folders")
        tree_header.addWidget(self.expand_all_button)
        tree_header.addWidget(self.collapse_all_button)
        left_layout.addLayout(tree_header)

        # The tree takes the stretch so it fills the panel's vertical space.
        self.image_tree = ImageTree()
        left_layout.addWidget(self.image_tree, 1)

        # Trash button below the image list: drops selected entries from the
        # project's image set (files on disk are untouched).
        icon_colour_for_trash = "#e0e0e0"
        self.remove_images_button = MaterialButton("  Remove from project", compact=True)
        self.remove_images_button.setIcon(qta.icon("mdi6.trash-can-outline", color=icon_colour_for_trash))
        self.remove_images_button.setIconSize(QSize(18, 18))
        self.remove_images_button.setToolTip("Drop the selected images from the project (files on disk are kept).")
        left_layout.addWidget(self.remove_images_button)

        icon_colour = "#e0e0e0"
        icon_size = QSize(20, 20)

        zoom_row = QHBoxLayout()
        zoom_row.setContentsMargins(0, 0, 0, 0)
        self.zoom_reset_button = QToolButton()
        self.zoom_reset_button.setIcon(qta.icon("mdi6.magnify", color=icon_colour))
        self.zoom_reset_button.setIconSize(icon_size)
        self.zoom_reset_button.setAutoRaise(True)
        self.zoom_reset_button.setToolTip("Reset zoom to fit")
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(_ZOOM_SLIDER_MIN, _ZOOM_SLIDER_MAX)
        self.zoom_slider.setValue(_ZOOM_SLIDER_DEFAULT)
        self.zoom_slider.setToolTip("Zoom")
        zoom_row.addWidget(self.zoom_reset_button)
        zoom_row.addWidget(self.zoom_slider, 1)
        left_layout.addLayout(zoom_row)

        brightness_row = QHBoxLayout()
        brightness_row.setContentsMargins(0, 0, 0, 0)
        self.brightness_reset_button = QToolButton()
        self.brightness_reset_button.setIcon(qta.icon("mdi6.brightness-6", color=icon_colour))
        self.brightness_reset_button.setIconSize(icon_size)
        self.brightness_reset_button.setAutoRaise(True)
        self.brightness_reset_button.setToolTip("Reset brightness")
        self.brightness_slider = QSlider(Qt.Horizontal)
        self.brightness_slider.setRange(_BRIGHTNESS_SLIDER_MIN, _BRIGHTNESS_SLIDER_MAX)
        self.brightness_slider.setValue(_BRIGHTNESS_SLIDER_DEFAULT)
        self.brightness_slider.setToolTip("Brightness")
        brightness_row.addWidget(self.brightness_reset_button)
        brightness_row.addWidget(self.brightness_slider, 1)
        left_layout.addLayout(brightness_row)
        left_panel.setLayout(left_layout)

        self.zoom_reset_button.clicked.connect(self._on_zoom_reset_clicked)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider_changed)
        self.brightness_reset_button.clicked.connect(self._on_brightness_reset_clicked)
        self.brightness_slider.valueChanged.connect(self._on_brightness_slider_changed)
        return left_panel

    def _build_right_panel(self) -> QWidget:
        """Build the right panel: scrolling AnnotationControlPanel + Clear All footer.

        The panel sits inside a QScrollArea so taller Auto Detect plugin
        stacks scroll instead of pushing the window past the screen.
        Clear All is a fixed footer below the scroll area so it stays
        visible regardless of how tall the panel contents grow.
        """
        self.annotation_controls = AnnotationControlPanel(self._detectors_by_kind)
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
        return right_panel

    @property
    def image_paths(self) -> list[str]:
        """Ordered list of image paths in the current project."""
        return self.project_store.image_paths()

    def _current_image_path(self) -> str | None:
        """Return the active image's path, or ``None`` when no image is loaded."""
        index = self.session.current_image_index
        paths = self.image_paths
        if 0 <= index < len(paths):
            return paths[index]
        return None

    def _mark_modified(self, modified: bool) -> None:
        """Slot for controller ``annotation_modified`` signals.

        Wrapping the property setter as a slot keeps the
        ``connect(controller.signal, slot)`` form readable.
        """
        self.session.modified = modified

    def _refresh_save_state_indicator(self) -> None:
        """Sync the window title to the current save state.

        Treated as saved when autosave is enabled (autosave keeps disk in sync
        on every image change) or when no edits are pending. Read-only
        sessions are tagged with a ``(read-only)`` suffix so the user can
        see at a glance that project-level edits won't persist.
        """
        saved = self.project_store.autosave or not self.session.modified
        ro = " (read-only)" if self.project_store.read_only else ""
        if 0 <= self.session.current_image_index < len(self.image_paths):
            name = Path(self.image_paths[self.session.current_image_index]).name
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
        self.expand_all_button.clicked.connect(self.image_tree.expandAll)
        self.collapse_all_button.clicked.connect(self.image_tree.collapseAll)
        self.image_tree.image_selected.connect(self.navigation_controller.on_image_selected)
        self.image_tree.remove_requested.connect(self.remove_images)

        self.annotation_controls.annotation_changed.connect(self.image_viewer.set_current_annotation)
        self.annotation_controls.fit_annotation_requested.connect(self.image_viewer.fit_annotation)
        self.annotation_controls.clear_selected_annotation_requested.connect(self.image_viewer.clear_selected_ellipse)
        self.annotation_controls.clear_pupil_requested.connect(self.image_viewer.clear_pupil_points)
        self.annotation_controls.clear_limbus_requested.connect(self.image_viewer.clear_limbus_points)
        self.annotation_controls.clear_eyelid_points_requested.connect(self.image_viewer.clear_eyelid_points)
        self.annotation_controls.clear_glint_points_requested.connect(self.image_viewer.clear_glint_points)
        self.annotation_controls.clear_all_requested.connect(self._on_clear_all)

        self.image_viewer.annotation_changed.connect(self.on_annotation_changed)
        self.image_viewer.annotation_type_changed.connect(self.annotation_controls.set_current_annotation)
        # On image change: drop both the orchestrator's per-image cache
        # AND the per-eye snapshot before the annotation_controller
        # restores whatever the new image's saved annotation carries.
        # The image viewer clears its own per-image overlay + kind-ROI
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
        self.session.current_image_index = 0 if self.image_paths else -1
        self.refresh_image_tree()
        if self.image_paths:
            self.load_current_image()

    def open_project(self, project_path: str) -> None:
        """Load ``project_path`` from disk and apply it as the active project."""
        try:
            self.project_store.load(project_path)
        except ProjectSchemaError as exc:
            QMessageBox.warning(
                self,
                "Cannot open this project",
                f"{exc} Pick another project file or start a new project.",
            )
            return
        self._apply_project_state()
        self.session.current_image_index = 0 if self.image_paths else -1
        self.refresh_image_tree()
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
        try:
            self.project_store.load_for_review(project_path, image_paths)
        except ProjectSchemaError as exc:
            QMessageBox.warning(
                self,
                "Cannot open this project",
                f"{exc} Pick another project file or start a new project.",
            )
            return
        self._apply_project_state()
        self.session.current_image_index = 0 if self.image_paths else -1
        self.refresh_image_tree()
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
        if self.project_store.path is not None:
            default_path = self.project_store.path
        else:
            default_path = str(Path(self._default_dialog_dir()) / f"untitled{PROJECT_FILE_SUFFIX}")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project As",
            default_path,
            f"Project Files (*{PROJECT_FILE_SUFFIX})",
        )
        if not path:
            return
        if not path.endswith(PROJECT_FILE_SUFFIX):
            path += PROJECT_FILE_SUFFIX
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
        valid = [
            str(Path(p).resolve()) for p in image_paths if Path(p).is_file() and Path(p).suffix.lower() in suffixes
        ]
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
            self.session.current_image_index = 0
        self.refresh_image_tree()
        if self.image_paths and self.session.current_image_index < 0:
            self.session.current_image_index = 0
        self.load_current_image()

    def add_images_from_folder(self, folder: str, recursive: bool = False) -> None:
        """Append every supported image under ``folder``.

        With ``recursive`` the whole subtree is walked; otherwise only the
        files directly inside ``folder`` are added.
        """
        suffixes = self.IMAGE_SUFFIXES
        entries = Path(folder).rglob("*") if recursive else Path(folder).iterdir()
        found = sorted(str(p) for p in entries if p.is_file() and p.suffix.lower() in suffixes)
        if not found:
            QMessageBox.warning(
                self,
                "No Images Found",
                f"No image files found in: {folder}",
            )
            return
        self.add_images(found)

    def remove_selected_images(self) -> None:
        """Drop the tree's currently-selected images from the project's image set."""
        self.remove_images(self.image_tree.selected_image_paths())

    def remove_images(self, paths: list[str]) -> None:
        """Drop ``paths`` from the project's image set; files on disk are untouched.

        Triggered by the trash button, the Delete / Backspace key, and the
        tree's folder / image removal context menu. Only the project's image
        dict is mutated — the real folders and image files stay as they are.
        """
        to_remove = [p for p in paths if p in self.image_paths]
        if not to_remove:
            return
        current_path = self._current_image_path()
        self.project_store.remove_images(to_remove)
        remaining = self.image_paths
        if current_path in to_remove or not remaining:
            self.session.current_image_index = 0 if remaining else -1
        else:
            self.session.current_image_index = remaining.index(current_path)
        self.refresh_image_tree()
        if remaining:
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
        self.detection_controller.apply_project_settings(project.get("detectors", {}))
        self.autosave_checkbox.blockSignals(True)
        self.autosave_checkbox.setChecked(self.project_store.autosave)
        self.autosave_checkbox.blockSignals(False)

    # ----- File-menu action stubs (wired by MenuHandler) ---------------

    def on_new_project(self) -> None:
        """File > New Project — show the wizard, then create + load the project."""
        dialog = NewProjectDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        result = dialog.result_payload()
        self.new_project(result["path"], result["project"])

    def _default_dialog_dir(self) -> str:
        """Return the current project's folder, or ``~/Desktop`` if none is loaded."""
        project_root = self.project_store.project_root()
        if project_root is not None:
            return str(project_root)
        return str(Path.home() / "Desktop")

    def on_open_project(self) -> None:
        """File > Open Project — pick a file, load it."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            self._default_dialog_dir(),
            f"Project Files (*{PROJECT_FILE_SUFFIX})",
        )
        if path:
            self.open_project(path)

    def on_project_settings(self) -> None:
        """File > Project Settings… — edit project-wide settings live."""
        if self.project_store.path is None:
            QMessageBox.information(
                self,
                "No project loaded",
                "Open or create a project before editing its settings.",
            )
            return
        dialog = ProjectSettingsDialog(self.project_store.project, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        updates = dialog.result_payload()
        self.project_store.project.update(updates)
        self.project_store.persist()
        self._apply_project_state()

    def on_load_images_clicked(self) -> None:
        """Left-panel "Load Images" button — file picker, appends to project."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Image Files",
            self._default_dialog_dir(),
            "Image Files (*.png *.jpg *.bmp)",
        )
        if files:
            self.add_images(files)

    def on_load_folder_clicked(self) -> None:
        """Left-panel "Load Images from Folder" button — folder picker, appends to project."""
        folder = QFileDialog.getExistingDirectory(self, "Select Image Folder", self._default_dialog_dir())
        if folder:
            self.add_images_from_folder(folder, recursive=self.recursive_folder_checkbox.isChecked())

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

    def refresh_image_tree(self) -> None:
        """Rebuild the folder tree from the current image set and reselect the active image.

        The tree groups the project's images by their real directories; the
        project's stored order is the source of truth for the current-image
        index, so only the presentation is rebuilt here.
        """
        self.image_tree.set_images(self.image_paths)
        if 0 <= self.session.current_image_index < len(self.image_paths):
            self.image_tree.select_path(self.image_paths[self.session.current_image_index])

    def load_current_image(self) -> None:
        """Load and display the current image with its annotations."""
        if 0 <= self.session.current_image_index < len(self.image_paths):
            image_path = self.image_paths[self.session.current_image_index]
            if self.image_viewer.load_image(image_path):
                self.setWindowTitle(f"EyE Annotation Tool - {Path(image_path).name}")
                self.annotation_controller.load_annotations()
                self.detection_controller.refresh_all_detections()
            else:
                QMessageBox.critical(self, "Error", f"Failed to load image: {image_path}")

    def on_annotation_changed(self) -> None:
        """Handle a manual-annotation edit: mark the project dirty."""
        self.session.modified = True

    def _on_autosave_changed(self, enabled: bool) -> None:
        """Persist the autosave toggle in project settings."""
        self.project_store.autosave = enabled
        self._refresh_save_state_indicator()

    def _on_zoom_reset_clicked(self) -> None:
        self.image_viewer.reset_zoom_to_fit()
        self._sync_zoom_slider_to_viewer()

    def _on_zoom_slider_changed(self, value: int) -> None:
        factor = _log_slider_to_factor(
            value,
            _ZOOM_SLIDER_MIN,
            _ZOOM_SLIDER_MAX,
            _ZOOM_MIN_FACTOR,
            _ZOOM_MAX_FACTOR,
        )
        self.image_viewer.set_zoom_factor(factor)

    def _sync_zoom_slider_to_viewer(self) -> None:
        slider_value = _factor_to_log_slider(
            self.image_viewer.zoom_state.factor,
            _ZOOM_SLIDER_MIN,
            _ZOOM_SLIDER_MAX,
            _ZOOM_MIN_FACTOR,
            _ZOOM_MAX_FACTOR,
        )
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(slider_value)
        self.zoom_slider.blockSignals(False)

    def _on_brightness_reset_clicked(self) -> None:
        self.image_viewer.reset_display_brightness()
        self._sync_brightness_slider_to_viewer()

    def _on_brightness_slider_changed(self, value: int) -> None:
        factor = _log_slider_to_factor(
            value,
            _BRIGHTNESS_SLIDER_MIN,
            _BRIGHTNESS_SLIDER_MAX,
            _BRIGHTNESS_MIN_FACTOR,
            _BRIGHTNESS_MAX_FACTOR,
        )
        self.image_viewer.set_brightness_factor(factor)

    def _sync_brightness_slider_to_viewer(self) -> None:
        slider_value = _factor_to_log_slider(
            self.image_viewer.brightness.factor,
            _BRIGHTNESS_SLIDER_MIN,
            _BRIGHTNESS_SLIDER_MAX,
            _BRIGHTNESS_MIN_FACTOR,
            _BRIGHTNESS_MAX_FACTOR,
        )
        self.brightness_slider.blockSignals(True)
        self.brightness_slider.setValue(slider_value)
        self.brightness_slider.blockSignals(False)

    def _on_clear_all(self) -> None:
        """Wipe every manual annotation AND every detection result on the current image."""
        self.image_viewer.clear_all()
        self.detection_controller.clear_all()

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
        """Filter window state changes to restore a sensible size when un-maximised."""
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMaximized:
                pass
            elif self.windowState() == Qt.WindowNoState:
                # When restored from maximised, set to 75% of the current screen.
                self.resize_to_percentage(0.75)
        return super().eventFilter(obj, event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Handle the window close event with autosave + unsaved-changes prompt."""
        if self.session.modified:
            if self.project_store.autosave:
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

    def show_about_dialog(self) -> None:
        """Show the Help > About dialog parented to the main window."""
        show_about_dialog(self)
