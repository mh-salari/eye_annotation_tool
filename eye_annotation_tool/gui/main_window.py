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
    QSplitter,
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
from ..state import PerEyeStateStore, ProjectStore, SessionState, UndoCoordinator, recent_projects
from ..utils.project_settings import (
    KINDS,
    PROJECT_FILE_SUFFIX,
    ProjectSchemaError,
    normalize_project_filename,
    strip_project_suffix,
)
from .about_dialog import show_about_dialog
from .annotation_controls import AnnotationControlPanel
from .collapsible import CollapsibleSection
from .compare_controls import CompareControls
from .custom_widgets import MaterialButton
from .dialogs import confirm, show_error
from .image_tree import ImageTree
from .image_viewer import ImageViewer
from .menu_handler import MenuHandler
from .new_project_dialog import NewProjectDialog
from .pair_strip import PairStrip
from .pairs_panel import PairsPanel
from .project_settings_dialog import ProjectSettingsDialog
from .shortcut_handler import ShortcutHandler
from .theme import theme

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

# Composable enhancement pipeline. Each method has an enable checkbox + one
# slider for its main param; enabled stages apply in this (denoise -> contrast
# -> sharpen) order. Tuple: (method, label, slider_min, slider_max,
# slider_default, params_from_slider, slider_from_params).
_ENHANCE_SPECS = (
    (
        "bilateral",
        "Bilateral",
        1,
        150,
        50,
        lambda v: {"sigma_color": float(v), "sigma_space": float(v)},
        lambda p: int(p.get("sigma_color", 50)),
    ),
    (
        "percentile_stretch",
        "Stretch",
        0,
        20,
        1,
        lambda v: {"lo_pct": float(v), "hi_pct": float(100 - v)},
        lambda p: int(p.get("lo_pct", 1)),
    ),
    ("clahe", "CLAHE", 5, 80, 20, lambda v: {"clip_limit": v / 10.0}, lambda p: round(p.get("clip_limit", 2.0) * 10)),
    ("gamma", "Gamma", 30, 300, 100, lambda v: {"g": v / 100.0}, lambda p: round(p.get("g", 1.0) * 100)),
    ("unsharp", "Unsharp", 0, 300, 100, lambda v: {"amount": v / 100.0}, lambda p: round(p.get("amount", 1.0) * 100)),
)


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
        self.project_store.set_error_handler(lambda exc: show_error(self, "Could Not Save Project", str(exc)))
        self.per_eye_state = PerEyeStateStore(KINDS)
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
            self.project_store,
            self.image_viewer,
            self.annotation_controls,
            parent=self,
        )
        self.detection_controller.annotation_modified.connect(self._mark_modified)
        self.detection_controller.status_message.connect(self.statusBar().showMessage)
        self.image_viewer.set_overlay_state_lookup(self.detection_controller.overlay_state_lookup)
        self.image_viewer.set_selection_lookup(self.detection_controller.selection_for)
        self.image_viewer.set_manual_fit_lookup(self.detection_controller.manual_fit_params)

        # Shared undo/redo across manual points and detector settings.
        self.undo_coordinator = UndoCoordinator(
            build_snapshot=self._build_undo_snapshot,
            apply_snapshot=self._apply_undo_snapshot,
            parent=self,
        )
        self.image_viewer.undo_coordinator = self.undo_coordinator
        self.detection_controller.undo_coordinator = self.undo_coordinator
        # In-app clipboard for copy/paste of detector settings (current eye).
        self._settings_clipboard: dict | None = None
        # Independent view settings (zoom / brightness / enhancement) for the two
        # modes: annotating a single image vs comparing a pair. Switching modes
        # saves the current mode's and applies the other's.
        self._view_settings: dict[str, dict | None] = {"annotation": None, "comparison": None}
        # The pairs-list index of the open pair, so prev/next can step through
        # pairs while comparing instead of through the image list.
        self._current_pair_index = -1

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
        left_panel.setMinimumWidth(180)
        right_panel = self._build_right_panel()

        # Horizontal splitter so the left panel (image tree) can be dragged
        # wider or narrower like a file-explorer sidebar; the viewer takes the slack.
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(self.image_viewer)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([300, 900, 360])
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)  # thin 1px divider like a file-explorer sidebar

        central_widget = QWidget()
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(splitter)
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
        self.auto_detect_checkbox = QCheckBox("Auto-detect on image load")
        self.auto_detect_checkbox.setToolTip(
            "On: opening an image runs the enabled detectors live.\n"
            "Off: the image shows only its saved detections; run detection "
            "with each card's Detect button."
        )
        self.auto_detect_checkbox.toggled.connect(self._on_auto_detect_changed)

        left_layout.addWidget(self.load_images_button)
        left_layout.addWidget(self.load_folder_button)
        left_layout.addWidget(self.recursive_folder_checkbox)
        left_layout.addWidget(self.prev_image_button)
        left_layout.addWidget(self.next_image_button)
        left_layout.addWidget(self.save_annotations_button)
        left_layout.addWidget(self.autosave_checkbox)
        left_layout.addWidget(self.auto_detect_checkbox)

        # Image list header (title + expand-all / collapse-all) sits with the tree
        # so the splitter resizes them as one pane.
        tree_container = QWidget()
        tree_box = QVBoxLayout(tree_container)
        tree_box.setContentsMargins(0, 0, 0, 0)
        tree_header = QHBoxLayout()
        tree_header.setContentsMargins(0, 0, 0, 0)
        tree_header.addWidget(QLabel("Loaded Images:"))
        tree_header.addStretch(1)
        self.expand_all_button = QToolButton()
        self.expand_all_button.setIcon(qta.icon("mdi6.expand-all", color=theme.color("icon")))
        self.expand_all_button.setAutoRaise(True)
        self.expand_all_button.setToolTip("Expand all folders")
        self.collapse_all_button = QToolButton()
        self.collapse_all_button.setIcon(qta.icon("mdi6.collapse-all", color=theme.color("icon")))
        self.collapse_all_button.setAutoRaise(True)
        self.collapse_all_button.setToolTip("Collapse all folders")
        tree_header.addWidget(self.expand_all_button)
        tree_header.addWidget(self.collapse_all_button)
        tree_box.addLayout(tree_header)
        self.image_tree = ImageTree()
        tree_box.addWidget(self.image_tree)

        # Vertical splitter so the image tree and compare-pairs list are drag-resizable.
        self.pairs_panel = PairsPanel()
        lists_splitter = QSplitter(Qt.Vertical)
        lists_splitter.addWidget(tree_container)
        lists_splitter.addWidget(self.pairs_panel)
        lists_splitter.setStretchFactor(0, 3)
        lists_splitter.setStretchFactor(1, 1)
        lists_splitter.setChildrenCollapsible(False)
        lists_splitter.setHandleWidth(8)
        # A centred grip so the divider reads as draggable (Qt's default handle is
        # an invisible thin line in the dark theme).
        lists_splitter.setStyleSheet(
            "QSplitter::handle:vertical { margin: 2px 24px; border-radius: 2px; background: palette(mid); }"
            "QSplitter::handle:vertical:hover { background: palette(light); }"
        )
        left_layout.addWidget(lists_splitter, 1)

        icon_colour = theme.color("icon")
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

        # Image enhancement: a composable pipeline (denoise -> contrast ->
        # sharpen). Each method has an enable checkbox + a slider for its main
        # param; enabled stages feed the display, and the detector too when
        # "Apply to detection" is ticked. Collapsed by default so it does not
        # crowd the lists on small screens.
        enhance_body = QWidget()
        enhance_layout = QVBoxLayout(enhance_body)
        enhance_layout.setContentsMargins(0, 0, 0, 0)
        self._enhance_checks: dict[str, QCheckBox] = {}
        self._enhance_sliders: dict[str, QSlider] = {}
        for method, label, smin, smax, sdef, _params_fn, _slider_fn in _ENHANCE_SPECS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            check = QCheckBox(label)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(smin, smax)
            slider.setValue(sdef)
            slider.setToolTip(f"{label} strength")
            row.addWidget(check)
            row.addWidget(slider, 1)
            enhance_layout.addLayout(row)
            self._enhance_checks[method] = check
            self._enhance_sliders[method] = slider
            check.toggled.connect(self._on_enhancement_changed)
            slider.valueChanged.connect(self._on_enhancement_changed)
        self.enhance_detect_check = QCheckBox("Apply to detection")
        self.enhance_detect_check.setToolTip("Also run the detector on the enhanced image")
        self.enhance_detect_check.toggled.connect(self._on_enhancement_changed)
        enhance_layout.addWidget(self.enhance_detect_check)
        enhance_section = CollapsibleSection("Enhance", expanded=False)
        enhance_section.add_widget(enhance_body)
        left_layout.addWidget(enhance_section)
        left_panel.setLayout(left_layout)

        self.zoom_reset_button.clicked.connect(self._on_zoom_reset_clicked)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider_changed)
        self.brightness_reset_button.clicked.connect(self._on_brightness_reset_clicked)
        self.brightness_slider.valueChanged.connect(self._on_brightness_slider_changed)
        return left_panel

    def _collect_enhance_stages(self) -> list:
        """Read the enabled methods + their slider params into an ordered pipeline."""
        stages = []
        for method, _label, _smin, _smax, _sdef, params_fn, _slider_fn in _ENHANCE_SPECS:
            if self._enhance_checks[method].isChecked():
                stages.append((method, params_fn(self._enhance_sliders[method].value())))
        return stages

    def _restore_enhancement(self, block: dict | None) -> None:
        """Restore the enhancement controls + viewer from a saved project block."""
        self.image_viewer.enhancement.from_dict(block)
        params_by_method = dict(self.image_viewer.enhancement.stages)
        for method, _label, _smin, _smax, _sdef, _params_fn, slider_fn in _ENHANCE_SPECS:
            enabled = method in params_by_method
            check = self._enhance_checks[method]
            slider = self._enhance_sliders[method]
            check.blockSignals(True)
            check.setChecked(enabled)
            check.blockSignals(False)
            if enabled:
                slider.blockSignals(True)
                slider.setValue(slider_fn(params_by_method[method]))
                slider.blockSignals(False)
        self.enhance_detect_check.blockSignals(True)
        self.enhance_detect_check.setChecked(self.image_viewer.enhancement.apply_to_detection)
        self.enhance_detect_check.blockSignals(False)
        # Refresh the viewer display to match the restored pipeline.
        self.image_viewer.apply_enhancement(self._collect_enhance_stages(), self.enhance_detect_check.isChecked())

    def _commit_enhancement_to_project(self) -> None:
        """Write the live enhancement into the project dict.

        Enhancement is a session/view setting: it is NOT written to the project
        file on change (that would persist eagerly like a structural setting).
        It reaches the file only through this call, made from the explicit
        project-save paths, so it survives reopen only when the user saved.
        """
        self.project_store.project["enhancement"] = self.image_viewer.enhancement.to_dict()

    def _on_enhancement_changed(self) -> None:
        """Apply the enhancement pipeline (session-only); re-run detection live + record undo.

        The pipeline lives in memory and carries between images; it is not
        persisted here. Provenance reaches disk per-image (in the annotation,
        when apply-to-detection is on) and project-wide only on explicit save.
        """
        stages = self._collect_enhance_stages()
        apply_to_detection = self.enhance_detect_check.isChecked()
        detector_input_changed = self.image_viewer.apply_enhancement(stages, apply_to_detection)
        if detector_input_changed:
            self.detection_controller.rerun_enabled_detectors()
        # Debounced so a continuous slider drag collapses into one undo step.
        self.undo_coordinator.capture_debounced()

    def _build_right_panel(self) -> QWidget:
        """Build the right panel: scrolling AnnotationControlPanel + Clear All footer.

        The panel sits inside a QScrollArea so taller Auto Detect plugin
        stacks scroll instead of pushing the window past the screen.
        Clear All is a fixed footer below the scroll area so it stays
        visible regardless of how tall the panel contents grow.
        """
        self.annotation_controls = AnnotationControlPanel(self._detectors_by_kind)
        self.right_scroll = QScrollArea()
        self.right_scroll.setWidget(self.annotation_controls)
        self.right_scroll.setWidgetResizable(True)
        self.right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.right_scroll.setFrameShape(QScrollArea.NoFrame)

        # Compare header replaces the cards while a pair is open.
        self.compare_controls = CompareControls(self.image_viewer)
        self.compare_controls.setVisible(False)
        # One pair strip at the top of the right panel, shown in both the
        # annotation view and compare mode when the current image is in a pair.
        self.pair_strip = PairStrip()
        self.pair_strip.setVisible(False)

        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.pair_strip)
        right_layout.addWidget(self.compare_controls)
        right_layout.addWidget(self.right_scroll, 1)
        right_layout.addWidget(self.annotation_controls.clear_all_button)
        right_panel.setLayout(right_layout)
        right_panel.setFixedWidth(360)  # 340 panel + room for the vertical scrollbar
        return right_panel

    def _set_compare_ui(self, on: bool) -> None:
        """Show the Compare header instead of the annotation cards while a pair is open."""
        self.compare_controls.setVisible(on)
        self.right_scroll.setVisible(not on)
        self.annotation_controls.clear_all_button.setVisible(not on)
        self._update_pair_strip()

    def _update_pair_strip(self) -> None:
        """Show the pair strip whenever the current image belongs to a usable pair.

        Shown at the top of the right panel in both the annotation view and compare
        mode; hidden unless compare is enabled and both of the pair's images are
        still in the project (so the buttons all work). While comparing, both A and
        B stay enabled; in the annotation view the loaded image's button is disabled.
        """
        current = self._current_image_path()
        enabled = bool(self.project_store.project.get("enable_compare", False))
        pair = next(
            (p for p in self.pairs_panel.pairs() if current in p and all(x in self.image_paths for x in p)),
            None,
        )
        if not enabled or pair is None:
            self.pair_strip.setVisible(False)
            return
        loaded = "" if self.image_viewer.comparing else current
        self.pair_strip.set_pair(pair[0], pair[1], loaded)
        self.pair_strip.setVisible(True)

    @property
    def image_paths(self) -> list[str]:
        """Ordered list of image paths in the current project."""
        return self.project_store.image_paths()

    def copy_settings(self) -> None:
        """Copy the active eye's detector selection, manual points and settings into the in-app clipboard."""
        self._settings_clipboard = {
            "selection": self.detection_controller.snapshot_selection(),
            "points": self.image_viewer.snapshot_points(),
            "params": self.detection_controller.snapshot_params(),
        }
        self.statusBar().showMessage("Settings copied.", 2000)

    def paste_settings(self) -> None:
        """Apply the clipboard's selection, manual points and settings onto the active eye and re-run.

        The selection is applied first so each kind switches to the copied
        detector; the manual points and params then follow so they land on that
        detector rather than on whatever was selected before.
        """
        if not self._settings_clipboard:
            return
        self.detection_controller.apply_selection(self._settings_clipboard["selection"])
        self.image_viewer.apply_points_state(self._settings_clipboard["points"])
        self.detection_controller.apply_params(self._settings_clipboard["params"])
        self.undo_coordinator.capture()
        self._mark_modified(True)
        self.statusBar().showMessage("Settings pasted.", 2000)

    def _build_undo_snapshot(self) -> dict:
        """Combined undo snapshot: detector selection + active-eye manual points + params."""
        return {
            "selection": self.detection_controller.snapshot_selection(),
            "points": self.image_viewer.snapshot_points(),
            "params": self.detection_controller.snapshot_params(),
            "enhancement": self.image_viewer.enhancement.to_dict(),
        }

    def _apply_undo_snapshot(self, snapshot: dict) -> None:
        """Restore a combined snapshot produced by :meth:`_build_undo_snapshot`."""
        self.detection_controller.apply_selection(snapshot["selection"])
        self.image_viewer.apply_points_state(snapshot["points"])
        self.detection_controller.apply_params(snapshot["params"])
        self._restore_enhancement(snapshot.get("enhancement"))

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
        """Sync the window title to the current project and save state.

        The title names the open project (not the current image — the image
        path lives in the viewer's breadcrumb). Treated as saved when autosave
        is enabled (autosave keeps disk in sync on every image change) or when
        no edits are pending. Read-only sessions are tagged with a
        ``(read-only)`` suffix so the user can see at a glance that
        project-level edits won't persist.
        """
        saved = self.project_store.autosave or not self.session.modified
        ro = " (read-only)" if self.project_store.read_only else ""
        project_name = strip_project_suffix(Path(self.project_store.path).name) if self.project_store.path else None
        if project_name:
            self.setWindowTitle(f"EyE Annotation Tool - {project_name}{'' if saved else ' *'}{ro}")
        else:
            self.setWindowTitle(f"EyE Annotation Tool{'' if saved else ' *'}{ro}")

    def connect_signals(self) -> None:
        """Connect signals and slots for UI components."""
        self.pairs_panel.add_requested.connect(self._on_add_pair)
        self.pairs_panel.open_requested.connect(self._on_open_pair)
        self.pairs_panel.pairs_changed.connect(self._on_pairs_changed)
        self.pair_strip.open_image_requested.connect(self._open_image_from_compare)
        self.pair_strip.open_compare_requested.connect(self._on_open_pair)
        self.load_images_button.clicked.connect(self.on_load_images_clicked)
        self.load_folder_button.clicked.connect(self.on_load_folder_clicked)
        self.prev_image_button.clicked.connect(self.navigate_prev)
        self.next_image_button.clicked.connect(self.navigate_next)
        self.save_annotations_button.clicked.connect(self.annotation_controller.save_annotations)
        self.expand_all_button.clicked.connect(self.image_tree.expandAll)
        self.collapse_all_button.clicked.connect(self.image_tree.collapseAll)
        self.image_tree.image_selected.connect(self.navigation_controller.on_image_selected)
        self.image_tree.remove_requested.connect(self.remove_images)
        theme.changed.connect(self._apply_theme_chrome)

        self.annotation_controls.fit_annotation_requested.connect(self.image_viewer.fit_annotation)
        self.annotation_controls.clear_selected_annotation_requested.connect(self.image_viewer.clear_selected_ellipse)
        self.annotation_controls.clear_all_requested.connect(self._on_clear_all)

        self.image_viewer.annotation_changed.connect(self.on_annotation_changed)
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
        recent_projects.add(project_path)
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
        recent_projects.add(project_path)
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
        self.detection_controller.save_settings_to_project()
        self._commit_enhancement_to_project()
        self.project_store.persist()
        self._refresh_save_state_indicator()

    def save_project_as(self) -> None:
        """Prompt for a save path and persist the active project there.

        Snapshotting clears the read-only flag: the chosen path becomes
        the new active project, and subsequent edits write through to it
        normally.
        """
        if self.project_store.path is not None:
            default_path = strip_project_suffix(self.project_store.path)
        else:
            default_path = str(Path(self._default_dialog_dir()) / "untitled")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project As",
            default_path,
            f"Project Files (*{PROJECT_FILE_SUFFIX})",
        )
        if not path:
            return
        path = normalize_project_filename(path)
        self.detection_controller.save_settings_to_project()
        self._commit_enhancement_to_project()
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

    def remove_images(self, paths: list[str]) -> None:
        """Drop ``paths`` from the project's image set; files on disk are untouched.

        Triggered by the row's inline x button, the Delete / Backspace key, and
        the tree's removal context menu. Only the project's image dict is
        mutated — the real folders and image files stay as they are.
        """
        to_remove = [p for p in paths if p in self.image_paths]
        if not to_remove:
            return
        plural = "s" if len(to_remove) != 1 else ""
        if not confirm(
            self,
            "Remove from project",
            f"Remove {len(to_remove)} image{plural} from the project?\nThe files on disk are kept.",
        ):
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
        self.pairs_panel.set_pairs(project.get("pairs", []))
        self.pairs_panel.setVisible(bool(project.get("enable_compare", False)))
        self._restore_enhancement(project.get("enhancement"))
        self.autosave_checkbox.blockSignals(True)
        self.autosave_checkbox.setChecked(self.project_store.autosave)
        self.autosave_checkbox.blockSignals(False)
        self.auto_detect_checkbox.blockSignals(True)
        self.auto_detect_checkbox.setChecked(self.project_store.auto_detect_on_load)
        self.auto_detect_checkbox.blockSignals(False)
        self._refresh_save_state_indicator()

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
        self._commit_enhancement_to_project()
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

    def _on_add_pair(self) -> None:
        """Pair the two images currently selected in the image list."""
        selected = self.image_tree.selected_image_paths()
        if len(selected) != 2:
            self.statusBar().showMessage("Select exactly two images in the list to pair them.", 3000)
            return
        self.pairs_panel.add_pair(selected[0], selected[1])

    def _on_open_pair(self, path_a: str, path_b: str) -> None:
        """Open a pair: load A as the current image, composite B onto it, show the controls."""
        if path_a not in self.image_paths or path_b not in self.image_paths:
            self.statusBar().showMessage("Both paired images must be in the project to compare them.", 3000)
            return
        # Persist (or prompt for) the current image's edits before switching, since
        # opening a pair reloads image A over whatever is in memory.
        if not self.navigation_controller.handle_unsaved_before_switch():
            return
        pairs = self.pairs_panel.pairs()
        self._current_pair_index = next(
            (i for i, (a, b) in enumerate(pairs) if a == path_a and b == path_b), self._current_pair_index
        )
        # Entering comparison from annotation: keep the annotation view settings.
        if not self.image_viewer.comparing:
            self._view_settings["annotation"] = self._capture_view_settings()
        self.session.current_image_index = self.image_paths.index(path_a)
        # Track the open pair in the pairs tree, not the image tree — stepping
        # through pairs should not drag the Loaded Images selection around.
        self.pairs_panel.select_pair(path_a, path_b)
        self.load_current_image()  # loads A in the normal view (and leaves any prior compare)
        self.image_viewer.enter_compare(path_a, path_b)
        self._set_compare_ui(True)
        # Apply comparison's own view settings, seeding them from the fitted
        # composite the first time (and syncing the sliders to the composite fit).
        if self._view_settings["comparison"] is None:
            self._view_settings["comparison"] = self._capture_view_settings()
            self._sync_zoom_slider_to_viewer()
            self._sync_brightness_slider_to_viewer()
        else:
            self._apply_view_settings(self._view_settings["comparison"])

    def navigate_next(self) -> None:
        """Next pair while comparing, otherwise the next image."""
        if self.image_viewer.comparing:
            self._open_pair_at_offset(1)
        else:
            self.navigation_controller.next_image()

    def navigate_prev(self) -> None:
        """Previous pair while comparing, otherwise the previous image."""
        if self.image_viewer.comparing:
            self._open_pair_at_offset(-1)
        else:
            self.navigation_controller.prev_image()

    def _open_pair_at_offset(self, delta: int) -> None:
        """Open the pair ``delta`` positions from the current one (clamped to the list)."""
        pairs = self.pairs_panel.pairs()
        if not pairs:
            return
        index = max(0, min(len(pairs) - 1, self._current_pair_index + delta))
        if index != self._current_pair_index:
            self._on_open_pair(*pairs[index])

    def _open_image_from_compare(self, path: str) -> None:
        """Open a paired image in the normal annotation view (leaving Compare)."""
        if path not in self.image_paths or not self.navigation_controller.handle_unsaved_before_switch():
            return
        self.session.current_image_index = self.image_paths.index(path)
        self.refresh_image_tree()
        self.load_current_image()

    def _on_pairs_changed(self) -> None:
        """Persist the edited pairs list to the project."""
        self.project_store.project["pairs"] = self.pairs_panel.pairs()
        self.project_store.persist()

    def load_current_image(self) -> None:
        """Load and display the current image with its annotations."""
        leaving_compare = self.image_viewer.comparing
        if leaving_compare:
            self._view_settings["comparison"] = self._capture_view_settings()
            self.image_viewer.exit_compare()
        self._set_compare_ui(False)
        if 0 <= self.session.current_image_index < len(self.image_paths):
            image_path = self.image_paths[self.session.current_image_index]
            if self.image_viewer.load_image(image_path):
                self.image_viewer.set_image_path_text(self._relative_image_path(image_path))
                self.annotation_controller.load_annotations()
                self.detection_controller.refresh_all_detections()
                # Seed undo history once the image's points + params are loaded.
                self.undo_coordinator.reset()
            else:
                QMessageBox.critical(self, "Error", f"Failed to load image: {image_path}")
        else:
            self.image_viewer.set_image_path_text("")
            self.undo_coordinator.reset()
        # Returning to annotation: restore that mode's saved view settings.
        if leaving_compare and self._view_settings["annotation"] is not None:
            self._apply_view_settings(self._view_settings["annotation"])

    def _relative_image_path(self, image_path: str) -> str:
        """Path of ``image_path`` relative to the project folder, or absolute if outside it."""
        root = self.project_store.project_root()
        if root is None:
            return Path(image_path).name
        try:
            return str(Path(image_path).relative_to(root))
        except ValueError:
            return image_path

    def on_annotation_changed(self) -> None:
        """Handle a manual-annotation edit: mark dirty + live-update manual pupil."""
        self.session.modified = True
        self.detection_controller.on_manual_edited()

    def _on_autosave_changed(self, enabled: bool) -> None:
        """Persist the autosave toggle in project settings."""
        self.project_store.autosave = enabled
        self._refresh_save_state_indicator()

    def _on_auto_detect_changed(self, enabled: bool) -> None:
        """Persist the auto-detect-on-load toggle and apply it to the current image.

        Turning it on runs the enabled detectors now so the effect is visible
        without navigating away; turning it off leaves the current overlays in
        place and only stops future automatic runs.
        """
        self.project_store.auto_detect_on_load = enabled
        if enabled:
            self.detection_controller.refresh_all_detections()

    def _apply_theme_chrome(self) -> None:
        """Re-colour the toolbar icons for the active theme (live theme switch)."""
        colour = theme.color("icon")
        self.expand_all_button.setIcon(qta.icon("mdi6.expand-all", color=colour))
        self.collapse_all_button.setIcon(qta.icon("mdi6.collapse-all", color=colour))
        self.zoom_reset_button.setIcon(qta.icon("mdi6.magnify", color=colour))
        self.brightness_reset_button.setIcon(qta.icon("mdi6.brightness-6", color=colour))

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

    def _capture_view_settings(self) -> dict:
        """Snapshot the current mode's zoom / brightness / enhancement view settings."""
        return {
            "zoom": self.image_viewer.zoom_state.factor,
            "at_fit": self.image_viewer.zoom_state.at_fit,
            "brightness": self.image_viewer.brightness.factor,
            "enhancement": self.image_viewer.enhancement.to_dict(),
        }

    def _apply_view_settings(self, settings: dict) -> None:
        """Apply a saved view-settings snapshot and sync the sliders + enhancement controls."""
        self._restore_enhancement(settings["enhancement"])
        self.image_viewer.set_brightness_factor(settings["brightness"])
        self._sync_brightness_slider_to_viewer()
        if settings["at_fit"]:
            self.image_viewer.reset_zoom_to_fit()
        else:
            self.image_viewer.set_zoom_factor(settings["zoom"])
        self._sync_zoom_slider_to_viewer()

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
        if self.project_store.autosave:
            # Mirror navigation: save the current image silently on exit so the
            # last image is persisted just like every one the user navigated past.
            self.annotation_controller.save_current_annotations(silent=True)
            event.accept()
            return
        if self.session.modified:
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
