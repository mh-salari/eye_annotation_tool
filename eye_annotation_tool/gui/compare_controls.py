"""Compare-mode header controls (right panel while a pair is open).

Choose the view (Overlay / Diff) and alignment, blend alpha, toggle the
difference heatmap, and nudge / rotate the partner image by hand. The pair strip
opens image A or B in the normal annotation view; overlay styling lives there,
not here.
"""

import qtawesome as qta
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .compare_compose import DIFF, OVERLAY, VIEWS
from .image_viewer import ImageViewer
from .pair_strip import PairStrip
from .theme import PRIMARY, theme

# Alignment modes mapped onto ImageViewer.set_compare_mode. "glints" matches the
# saved glints; "manual" exposes the nudge / rotate controls; "none" overlays raw.
_ALIGNMENTS = ("glints", "manual", "none")
_NUDGE_PX = 1.0
_ROTATE_DEG = 0.5


class SegmentedControl(QWidget):
    """A row of connected toggle buttons for one-click selection of one option.

    Each option is a label, or a ``(label, value)`` pair when the displayed text
    should differ from the value.
    """

    selection_changed = pyqtSignal(str)

    def __init__(self, options: list, parent: QWidget | None = None) -> None:
        """Build one toggle button per option, with the first selected."""
        super().__init__(parent)
        self._values: list[str] = []
        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self._group = QButtonGroup(self)
        self._buttons: list[QToolButton] = []
        for index, option in enumerate(options):
            label, value = option if isinstance(option, tuple) else (option, option)
            self._values.append(value)
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._group.addButton(button, index)
            box.addWidget(button)
            self._buttons.append(button)
        self._group.idClicked.connect(lambda i: self.selection_changed.emit(self._values[i]))
        self.setStyleSheet(
            "QToolButton { padding: 4px 2px; border: 1px solid palette(mid); }"
            f"QToolButton:checked {{ background: {PRIMARY}; color: white; border-color: {PRIMARY}; }}"
        )

    def current_value(self) -> str:
        """The selected option's value."""
        return self._values[self._group.checkedId()]

    def set_current_value(self, value: str) -> None:
        """Select the option whose value is ``value`` and emit the change."""
        if value in self._values:
            self._buttons[self._values.index(value)].setChecked(True)
            self.selection_changed.emit(value)


class CompareControls(QWidget):
    """Drive the viewer's composite and emit a request to open A or B for annotation."""

    # Path of the image to open in the normal annotation view.
    open_image_requested = pyqtSignal(str)

    def __init__(self, viewer: ImageViewer, parent: QWidget | None = None) -> None:
        """Build the controls bound to ``viewer``."""
        super().__init__(parent)
        self._viewer = viewer
        self.nudge_buttons: list[QToolButton] = []
        self.rotate_buttons: list[QToolButton] = []
        self._build_ui()
        self._sync_visibility()

    def set_pair(self, path_a: str, path_b: str) -> None:
        """Point the pair strip at the open pair (both A and B editable)."""
        self.pair_strip.set_pair(path_a, path_b)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(QLabel("<b>Compare</b>"))

        self.view_selector = SegmentedControl(list(VIEWS))
        self.view_selector.selection_changed.connect(self._on_view_changed)
        layout.addWidget(self._labelled("View", self.view_selector))

        self.align_selector = SegmentedControl(list(_ALIGNMENTS))
        self.align_selector.selection_changed.connect(self._on_align_changed)
        layout.addWidget(self._labelled("Align", self.align_selector))

        self.alpha_slider = QSlider(Qt.Horizontal)
        self.alpha_slider.setRange(0, 100)
        self.alpha_slider.setValue(50)
        self.alpha_slider.setToolTip("Blend weight of image B in the overlay")
        self.alpha_slider.valueChanged.connect(lambda v: self._viewer.set_compare_alpha(v / 100.0))
        self.alpha_row = self._labelled("Alpha", self.alpha_slider)
        layout.addWidget(self.alpha_row)

        self.diff_colormap_check = QCheckBox("Heatmap")
        self.diff_colormap_check.setToolTip("Colour the difference as a warm heatmap instead of grey")
        self.diff_colormap_check.setChecked(True)
        self.diff_colormap_check.toggled.connect(self._viewer.set_compare_diff_colormap)
        layout.addWidget(self.diff_colormap_check)

        self.nudge_pad = self._nudge_rotate_pad()
        layout.addWidget(self.nudge_pad)

        self.pair_strip = PairStrip(show_compare=False)
        self.pair_strip.open_image_requested.connect(self.open_image_requested.emit)
        layout.addWidget(self.pair_strip)

        layout.addStretch(1)

    def _nudge_rotate_pad(self) -> QWidget:
        """A D-pad of nudge arrows with the two rotate buttons in the top corners."""
        pad = QWidget()
        grid = QGridLayout(pad)
        grid.setContentsMargins(0, 0, 0, 0)
        for icon, tip, delta, row, col in (
            ("mdi6.arrow-up", "Nudge B up", (0.0, -_NUDGE_PX), 0, 1),
            ("mdi6.arrow-left", "Nudge B left", (-_NUDGE_PX, 0.0), 1, 0),
            ("mdi6.arrow-right", "Nudge B right", (_NUDGE_PX, 0.0), 1, 2),
            ("mdi6.arrow-down", "Nudge B down", (0.0, _NUDGE_PX), 2, 1),
        ):
            button = self._icon_button(icon, tip)
            button.clicked.connect(lambda _checked=False, d=delta: self._viewer.nudge_compare(*d))
            grid.addWidget(button, row, col)
            self.nudge_buttons.append(button)
        for icon, tip, degrees, row, col in (
            ("mdi6.rotate-left", "Rotate B counter-clockwise", -_ROTATE_DEG, 0, 0),
            ("mdi6.rotate-right", "Rotate B clockwise", _ROTATE_DEG, 0, 2),
        ):
            button = self._icon_button(icon, tip)
            button.clicked.connect(lambda _checked=False, d=degrees: self._viewer.rotate_compare(d))
            grid.addWidget(button, row, col)
            self.rotate_buttons.append(button)
        return pad

    @staticmethod
    def _icon_button(icon: str, tip: str) -> QToolButton:
        """An auto-raise tool button with the themed ``icon`` and a tooltip."""
        button = QToolButton()
        button.setIcon(qta.icon(icon, color=theme.color("icon")))
        button.setAutoRaise(True)
        button.setToolTip(tip)
        return button

    @staticmethod
    def _labelled(text: str, widget: QWidget) -> QWidget:
        """A ``label: widget`` row wrapped in a widget so it can be shown/hidden."""
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        label = QLabel(text)
        label.setFixedWidth(48)
        box.addWidget(label)
        box.addWidget(widget, 1)
        return row

    def _on_align_changed(self, mode: str) -> None:
        self._viewer.set_compare_mode(mode)
        self._sync_visibility()

    def _on_view_changed(self, view: str) -> None:
        self._viewer.set_compare_view(view)
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        """Show only the controls that apply to the current view / alignment."""
        view = self.view_selector.current_value()
        self.alpha_row.setVisible(view == OVERLAY)
        self.diff_colormap_check.setVisible(view == DIFF)
        self.nudge_pad.setVisible(self.align_selector.current_value() == "manual")
