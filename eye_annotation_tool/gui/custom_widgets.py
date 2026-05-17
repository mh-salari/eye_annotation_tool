"""Custom widget components for the application."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class MaterialButton(QPushButton):
    """Custom styled button with material design appearance.

    ``compact=True`` shrinks the font size and padding so several
    buttons fit in a single panel row without truncating their labels.
    """

    def __init__(self, text: str, parent: QWidget | None = None, *, compact: bool = False) -> None:
        """Initialize the MaterialButton."""
        super().__init__(text, parent)
        font_size = "11px" if compact else "14px"
        padding = "5px 10px" if compact else "10px 20px"
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: #3a3a3a;
                border: 1px solid #555;
                color: #e0e0e0;
                padding: {padding};
                text-align: center;
                text-decoration: none;
                font-size: {font_size};
                margin: 4px 2px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #d9534f;
                border: 1px solid #c9302c;
            }}
            QPushButton:pressed {{
                background-color: #ac2925;
            }}
            QPushButton:checked {{
                background-color: #4caf50;
                border: 1px solid #388e3c;
                color: white;
            }}
            QPushButton:disabled {{
                color: #777;
                border: 1px solid #444;
            }}
            """
        )


class IconButton(QPushButton):
    """Icon-based button for compact UI."""

    def __init__(self, icon: str, tooltip: str, parent: QWidget | None = None) -> None:
        """Initialize the IconButton."""
        super().__init__(icon, parent)
        self.setToolTip(tooltip)
        self.setFixedHeight(28)
        self.setStyleSheet(
            """
            QPushButton {
                background-color: #3a3a3a;
                border: 1px solid #555;
                color: #e0e0e0;
                font-size: 11px;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #007f76;
                border: 1px solid #009688;
            }
            QPushButton:pressed {
                background-color: #005f56;
            }
        """
        )


class ClearIconButton(QPushButton):
    """Clear icon button with destructive styling."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the ClearIconButton."""
        super().__init__("x", parent)
        self.setToolTip("Clear")
        self.setFixedHeight(28)
        self.setFixedWidth(32)
        self.setStyleSheet(
            """
            QPushButton {
                background-color: #3a3a3a;
                border: 1px solid #555;
                color: #d9534f;
                font-size: 12px;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #d9534f;
                border: 1px solid #c9302c;
                color: white;
            }
            QPushButton:pressed {
                background-color: #ac2925;
            }
        """
        )


class AnnotationGroup(QGroupBox):
    """Grouped card widget for an annotation type in Manual mode."""

    selected = pyqtSignal()
    fit_requested = pyqtSignal()
    clear_requested = pyqtSignal()

    def __init__(
        self,
        title: str,
        has_fit: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """Initialise the AnnotationGroup."""
        super().__init__(parent)
        self.has_fit = has_fit
        self.setup_ui(title)

    def setup_ui(self, title: str) -> None:
        """Set up the user interface for the annotation group."""
        self.setStyleSheet(
            """
            QGroupBox {
                border: 1px solid #555;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 10px;
                background-color: #2b2b2b;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #00bcd4;
                font-weight: bold;
            }
            QRadioButton {
                color: #e0e0e0;
                spacing: 5px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
            }
            QRadioButton::indicator:unchecked {
                border: 2px solid #555;
                background: #3a3a3a;
                border-radius: 8px;
            }
            QRadioButton::indicator:checked {
                border: 2px solid #00bcd4;
                background: #00bcd4;
                border-radius: 8px;
            }
        """
        )

        layout = QVBoxLayout()

        self.radio = QRadioButton(title)
        self.radio.clicked.connect(self.selected.emit)
        layout.addWidget(self.radio)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(4)

        if self.has_fit:
            self.fit_button = IconButton("fit ellipse", "Fit Ellipse")
            self.fit_button.clicked.connect(self.fit_requested.emit)
            button_layout.addWidget(self.fit_button)

        self.clear_button = ClearIconButton()
        self.clear_button.clicked.connect(self.clear_requested.emit)
        button_layout.addWidget(self.clear_button)

        button_layout.addStretch()

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def is_checked(self) -> bool:
        """Check if the annotation type is selected."""
        return self.radio.isChecked()

    def set_checked(self, checked: bool) -> None:
        """Set the checked state of the annotation type."""
        self.radio.setChecked(checked)


class EyeSelector(QGroupBox):
    """Selector for binocular vs monocular images and active eye in binocular mode.

    A **Binocular** checkbox toggles whether the image contains two eyes.
    When checked, a Left / Right radio pair is shown so the user can pick
    which eye the canvas + manual + auto-detect workflow is currently
    operating on. When unchecked, the image is treated as a single eye
    with no left/right distinction — the radios are hidden.

    Two independent signals carry the selector's state outward:

    - :pyattr:`binocular_toggled` — emitted when the checkbox flips.
    - :pyattr:`eye_changed` — emitted when the Left / Right radio
      changes (only meaningful when binocular).
    """

    eye_changed = pyqtSignal(str)
    binocular_toggled = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the EyeSelector."""
        super().__init__("Eye Selection", parent)
        self.setup_ui()

    def setup_ui(self) -> None:
        """Set up the user interface for the eye selector."""
        self.setStyleSheet(
            """
            QGroupBox {
                border: 1px solid #555;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 10px;
                background-color: #2b2b2b;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #00bcd4;
                font-weight: bold;
            }
            QCheckBox, QRadioButton {
                color: #e0e0e0;
                spacing: 5px;
                padding: 5px;
            }
            QCheckBox:disabled, QRadioButton:disabled {
                color: #666;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
            }
            QRadioButton::indicator:unchecked {
                border: 2px solid #555;
                background: #3a3a3a;
                border-radius: 8px;
            }
            QRadioButton::indicator:checked {
                border: 2px solid #00bcd4;
                background: #00bcd4;
                border-radius: 8px;
            }
            QRadioButton::indicator:disabled {
                border: 2px solid #3a3a3a;
                background: #2b2b2b;
            }
            """
        )

        layout = QVBoxLayout()
        layout.setSpacing(4)

        self.binocular_check = QCheckBox("Binocular image")
        self.binocular_check.setChecked(True)
        self.binocular_check.toggled.connect(self._on_binocular_toggled)
        layout.addWidget(self.binocular_check)

        self.eye_row = QWidget()
        eye_layout = QHBoxLayout(self.eye_row)
        eye_layout.setContentsMargins(0, 0, 0, 0)
        eye_layout.setSpacing(10)
        self.left_eye_radio = QRadioButton("Left Eye")
        self.right_eye_radio = QRadioButton("Right Eye")
        self.left_eye_radio.setChecked(True)
        self.button_group = QButtonGroup(self)
        self.button_group.addButton(self.left_eye_radio)
        self.button_group.addButton(self.right_eye_radio)
        self.left_eye_radio.clicked.connect(lambda: self.eye_changed.emit("left"))
        self.right_eye_radio.clicked.connect(lambda: self.eye_changed.emit("right"))
        eye_layout.addWidget(self.left_eye_radio)
        eye_layout.addWidget(self.right_eye_radio)
        eye_layout.addStretch()
        layout.addWidget(self.eye_row)

        self.setLayout(layout)

    def _on_binocular_toggled(self, checked: bool) -> None:
        """Show / hide the Left-Right radios and forward the toggle."""
        self.eye_row.setVisible(checked)
        self.binocular_toggled.emit(checked)

    def is_binocular(self) -> bool:
        """Return ``True`` when the user has marked the image as binocular."""
        return self.binocular_check.isChecked()

    def set_binocular(self, enabled: bool) -> None:
        """Set the binocular checkbox without emitting ``binocular_toggled``."""
        self.binocular_check.blockSignals(True)
        self.binocular_check.setChecked(enabled)
        self.binocular_check.blockSignals(False)
        self.eye_row.setVisible(enabled)

    def get_current_eye(self) -> str:
        """Return ``"left"`` or ``"right"`` based on the active radio.

        The return value is only meaningful when :meth:`is_binocular` is
        ``True``; in monocular mode the caller should use the image-wide
        flat data store instead.
        """
        return "left" if self.left_eye_radio.isChecked() else "right"

    def set_current_eye(self, eye: str) -> None:
        """Set the currently selected eye (``"left"`` / ``"right"``) without emitting."""
        radio = self.right_eye_radio if eye == "right" else self.left_eye_radio
        radio.blockSignals(True)
        radio.setChecked(True)
        radio.blockSignals(False)


class GateRow(QWidget):
    """One-line panel control for a 50-100 % shape-quality gate.

    Wraps a labelled :class:`QCheckBox` (enable/disable the gate),
    a horizontal :class:`QSlider` and a linked :class:`QSpinBox`.
    The slider and spinbox track each other; the spinbox always
    displays the value with a ``%`` suffix. Two signals carry user
    interaction outward:

    - :pyattr:`toggled(bool)` — checkbox flipped; the slider / spinbox
      are greyed / enabled to match before this fires.
    - :pyattr:`pct_changed(int)` — slider (or linked spinbox) value
      changed.

    Used for the Min ellipse fit + Min roundness rows in the Threshold
    Pupil and Threshold Glint plugin panels.
    """

    toggled = pyqtSignal(bool)
    pct_changed = pyqtSignal(int)

    def __init__(
        self,
        label: str,
        *,
        initial_enabled: bool,
        initial_pct: int,
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """Build the row pre-populated with ``(initial_enabled, initial_pct)``."""
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.check = QCheckBox(label)
        self.check.setChecked(initial_enabled)
        self.check.setToolTip(tooltip)
        self.check.setMinimumWidth(140)
        self.check.toggled.connect(self._on_toggled)
        layout.addWidget(self.check)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(50, 100)
        self.slider.setValue(initial_pct)
        self.slider.setEnabled(initial_enabled)
        self.slider.setToolTip(tooltip)
        self.spin = QSpinBox()
        self.spin.setRange(50, 100)
        self.spin.setValue(initial_pct)
        self.spin.setSuffix(" %")
        self.spin.setEnabled(initial_enabled)
        self.spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin.setMinimumWidth(60)
        self.spin.setMaximumWidth(80)
        self.spin.setToolTip(tooltip)
        # Slider drives the spinbox and vice versa; only the slider's
        # valueChanged is forwarded to ``pct_changed`` so listeners see
        # a single emission per user action.
        self.slider.valueChanged.connect(self.spin.setValue)
        self.spin.valueChanged.connect(self.slider.setValue)
        self.slider.valueChanged.connect(self.pct_changed.emit)
        layout.addWidget(self.slider)
        layout.addWidget(self.spin)

    def _on_toggled(self, checked: bool) -> None:
        self.slider.setEnabled(checked)
        self.spin.setEnabled(checked)
        self.toggled.emit(checked)

    def is_checked(self) -> bool:
        """Return the gate's enable flag."""
        return self.check.isChecked()

    def pct(self) -> int:
        """Return the gate's current percentage value."""
        return self.spin.value()

    def set_state(self, *, enabled: bool, pct: int) -> None:
        """Silently restore the row to ``(enabled, pct)`` for ``set_params`` round-trips."""
        widgets = (self.check, self.slider, self.spin)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.check.setChecked(enabled)
            self.slider.setEnabled(enabled)
            self.spin.setEnabled(enabled)
            self.slider.setValue(pct)
            self.spin.setValue(pct)
        finally:
            for w in widgets:
                w.blockSignals(False)
