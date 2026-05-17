"""Right-side control panel.

Top section is a two-button mode switcher (Manual / Auto Detect). The
remaining panel content is a QStackedWidget whose pages correspond to
the two modes:

  - **Manual** — pure click-to-place annotation. Annotation type radios
    (Pupil / Limbus / Eyelid / Glint), Fit / Clear actions per type, eye
    selector. No detector controls.
  - **Auto Detect** — placeholder in this refactor step; the next step
    wires the enabled plugins' panels into this page.

Clear All sits at the bottom and dispatches to the image viewer.
"""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .custom_widgets import AnnotationGroup, EyeSelector, MaterialButton

MODE_MANUAL = "manual"
MODE_AUTO_DETECT = "auto_detect"


class AnnotationControlPanel(QWidget):
    """Right-panel widget hosting the mode switcher and per-mode pages."""

    annotation_changed = pyqtSignal(str)
    eye_changed = pyqtSignal(str)
    fit_annotation_requested = pyqtSignal()
    clear_pupil_requested = pyqtSignal()
    clear_limbus_requested = pyqtSignal()
    clear_eyelid_points_requested = pyqtSignal()
    clear_glint_points_requested = pyqtSignal()
    clear_all_requested = pyqtSignal()
    clear_selected_annotation_requested = pyqtSignal()
    # Emitted when the top mode switcher flips. Carries one of the MODE_* slugs.
    mode_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialise the AnnotationControlPanel."""
        super().__init__(parent)
        # Fixed width keeps the window stable when the mode switcher swaps
        # pages with different intrinsic widths.
        self.setFixedWidth(340)
        self.setup_ui()

    def setup_ui(self) -> None:
        """Build the eye selector, mode switcher, page stack, and Clear All."""
        layout = QVBoxLayout()

        self.eye_selector = EyeSelector()
        self.eye_selector.eye_changed.connect(self.eye_changed.emit)
        layout.addWidget(self.eye_selector)

        # Mode switcher: two exclusive checkable buttons act as a segmented
        # control. Manual is the default at startup.
        self.mode_manual_button = MaterialButton("Manual")
        self.mode_manual_button.setCheckable(True)
        self.mode_manual_button.setChecked(True)
        self.mode_auto_detect_button = MaterialButton("Auto Detect")
        self.mode_auto_detect_button.setCheckable(True)
        self.mode_button_group = QButtonGroup(self)
        self.mode_button_group.setExclusive(True)
        self.mode_button_group.addButton(self.mode_manual_button)
        self.mode_button_group.addButton(self.mode_auto_detect_button)
        self.mode_manual_button.toggled.connect(
            lambda checked: checked and self._apply_mode(MODE_MANUAL, emit=True),
        )
        self.mode_auto_detect_button.toggled.connect(
            lambda checked: checked and self._apply_mode(MODE_AUTO_DETECT, emit=True),
        )
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_manual_button)
        mode_row.addWidget(self.mode_auto_detect_button)
        layout.addLayout(mode_row)

        # Mode-specific pages live in a QStackedWidget so swapping pages
        # doesn't change the panel's total height and Clear All stays put.
        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self._build_manual_page())
        self.auto_detect_page = self._build_auto_detect_page()
        self.mode_stack.addWidget(self.auto_detect_page)
        layout.addWidget(self.mode_stack)

        layout.addStretch(1)

        # Clear All is created here so its signal wiring stays with the
        # rest of the panel, but it's NOT added to ``layout`` — MainWindow
        # pins it outside the scrollable area so it remains visible in
        # both modes regardless of how tall the plugin-panel stack grows.
        self.clear_all_button = MaterialButton("Clear All")
        self.clear_all_button.clicked.connect(self.clear_all_requested.emit)

        self.setLayout(layout)

        self._current_mode = MODE_MANUAL
        self._apply_mode(MODE_MANUAL, emit=False)

    def _build_manual_page(self) -> QWidget:
        """Build the Manual-mode page: annotation type radios + per-type actions."""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.annotation_types_title = QLabel("Annotation Types")
        self.annotation_types_title.setStyleSheet(
            """
            QLabel {
                font-size: 16px;
                font-weight: bold;
                color: #00bcd4;
                padding: 10px 0;
            }
            """,
        )
        layout.addWidget(self.annotation_types_title)

        self.pupil_group = AnnotationGroup("Pupil", has_fit=True)
        self.pupil_group.selected.connect(lambda: self.annotation_changed.emit("pupil"))
        self.pupil_group.fit_requested.connect(self.fit_annotation_requested.emit)
        self.pupil_group.clear_requested.connect(self.clear_pupil_requested.emit)
        self.pupil_group.set_checked(True)

        self.limbus_group = AnnotationGroup("Limbus", has_fit=True)
        self.limbus_group.selected.connect(lambda: self.annotation_changed.emit("limbus"))
        self.limbus_group.fit_requested.connect(self.fit_annotation_requested.emit)
        self.limbus_group.clear_requested.connect(self.clear_limbus_requested.emit)

        self.eyelid_group = AnnotationGroup("Eyelid Contour", has_fit=False)
        self.eyelid_group.selected.connect(lambda: self.annotation_changed.emit("eyelid_contour"))
        self.eyelid_group.clear_requested.connect(self.clear_eyelid_points_requested.emit)

        self.glint_group = AnnotationGroup("Glint", has_fit=False)
        self.glint_group.selected.connect(lambda: self.annotation_changed.emit("glint"))
        self.glint_group.clear_requested.connect(self.clear_glint_points_requested.emit)

        self.button_group = QButtonGroup()
        self.button_group.addButton(self.pupil_group.radio)
        self.button_group.addButton(self.limbus_group.radio)
        self.button_group.addButton(self.eyelid_group.radio)
        self.button_group.addButton(self.glint_group.radio)

        layout.addWidget(self.pupil_group)
        layout.addWidget(self.limbus_group)
        layout.addWidget(self.eyelid_group)
        layout.addWidget(self.glint_group)
        layout.addStretch(1)
        page.setLayout(layout)
        return page

    def _build_auto_detect_page(self) -> QWidget:
        """Build the Auto Detect page: a dynamic stack of plugin panels.

        The stack starts empty; ``set_auto_detect_panels`` is called by
        MainWindow on every project-settings change to install the panels
        for the currently enabled plugins. Cheap plugins re-run live on
        slider drag via their ``params_changed`` signal; expensive
        plugins (e.g. Daugman limbus, once it lands) provide their own
        per-plugin Detect button inside their panel. There is no global
        "Run Auto Detect" button.
        """
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # Container that holds one plugin panel per enabled target. Replaced
        # wholesale by set_auto_detect_panels — old widgets are deleted so
        # their signal connections drop with them.
        self._auto_detect_panels_container = QVBoxLayout()
        self._auto_detect_panels_container.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._auto_detect_panels_container)

        # Empty-state notice; hidden once any panel is mounted.
        self._auto_detect_empty_label = QLabel(
            "No Auto Detect plugin is enabled for this project.",
        )
        self._auto_detect_empty_label.setWordWrap(True)
        self._auto_detect_empty_label.setStyleSheet("QLabel { color: #888888; padding: 12px; }")
        layout.addWidget(self._auto_detect_empty_label)

        layout.addStretch(1)

        page.setLayout(layout)
        self._auto_detect_panels: dict[str, QWidget] = {}
        return page

    def set_auto_detect_panels(self, panels: list[tuple[str, QWidget]]) -> None:
        """Replace the Auto Detect page's plugin-panel stack.

        ``panels`` is a list of ``(plugin_name, panel_widget)`` pairs in the
        order they should be displayed top-to-bottom. Previously installed
        panel widgets are removed from the layout and scheduled for
        deletion via ``deleteLater`` so their signal connections drop.
        """
        while self._auto_detect_panels_container.count():
            item = self._auto_detect_panels_container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._auto_detect_panels.clear()
        for name, widget in panels:
            self._auto_detect_panels_container.addWidget(widget)
            self._auto_detect_panels[name] = widget
        self._auto_detect_empty_label.setVisible(not panels)

    def auto_detect_panel(self, plugin_name: str) -> QWidget | None:
        """Return the currently mounted Auto Detect panel for ``plugin_name``, or None."""
        return self._auto_detect_panels.get(plugin_name)

    def auto_detect_plugin_names(self) -> list[str]:
        """Return the slugs of every currently mounted Auto Detect plugin."""
        return list(self._auto_detect_panels.keys())

    def set_current_annotation(self, annotation_type: str) -> None:
        """Tick the radio for ``annotation_type`` (pupil/limbus/eyelid_contour/glint)."""
        if annotation_type == "pupil":
            self.pupil_group.set_checked(True)
        elif annotation_type == "limbus":
            self.limbus_group.set_checked(True)
        elif annotation_type == "eyelid_contour":
            self.eyelid_group.set_checked(True)
        else:
            self.glint_group.set_checked(True)

    def get_current_annotation_type(self) -> str:
        """Return the radio-selected annotation type slug."""
        if self.pupil_group.is_checked():
            return "pupil"
        if self.limbus_group.is_checked():
            return "limbus"
        if self.eyelid_group.is_checked():
            return "eyelid_contour"
        return "glint"

    def get_current_eye(self) -> str:
        """Return the currently selected eye (``"left"`` / ``"right"`` / ``"single"``)."""
        return self.eye_selector.get_current_eye()

    def set_current_eye(self, eye: str) -> None:
        """Set the currently selected eye."""
        self.eye_selector.set_current_eye(eye)

    def set_single_eye_mode(self, enabled: bool) -> None:
        """Reflect single-eye mode in the eye selector radio."""
        self.eye_selector.set_current_eye("single" if enabled else "left")

    def current_mode(self) -> str:
        """Return the current mode slug (one of MODE_MANUAL / MODE_AUTO_DETECT)."""
        return self._current_mode

    def _apply_mode(self, mode: str, *, emit: bool) -> None:
        """Swap the stacked page for ``mode``; emit ``mode_changed`` when ``emit`` is True."""
        self._current_mode = mode
        self.mode_stack.setCurrentIndex(0 if mode == MODE_MANUAL else 1)
        if emit:
            self.mode_changed.emit(mode)
