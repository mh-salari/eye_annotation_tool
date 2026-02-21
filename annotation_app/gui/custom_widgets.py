"""Custom widget components for the application."""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QButtonGroup, QGroupBox, QHBoxLayout, QPushButton, QRadioButton, QVBoxLayout, QWidget


class MaterialButton(QPushButton):
    """Custom styled button with material design appearance."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        """Initialize the MaterialButton."""
        super().__init__(text, parent)
        self.setStyleSheet(
            """
            QPushButton {
                background-color: #3a3a3a;
                border: 1px solid #555;
                color: #e0e0e0;
                padding: 10px 20px;
                text-align: center;
                text-decoration: none;
                font-size: 14px;
                margin: 4px 2px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #d9534f;
                border: 1px solid #c9302c;
            }
            QPushButton:pressed {
                background-color: #ac2925;
            }
            QPushButton:checked {
                background-color: #4caf50;
                border: 1px solid #388e3c;
                color: white;
            }
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
    """Grouped card widget for annotation type controls."""

    selected = pyqtSignal()
    fit_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    ai_assist_requested = pyqtSignal()
    roi_requested = pyqtSignal()

    def __init__(
        self,
        title: str,
        has_fit: bool = True,
        has_ai_assist: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the AnnotationGroup."""
        super().__init__(parent)
        self.has_fit = has_fit
        self.has_ai_assist = has_ai_assist
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

        if self.has_ai_assist:
            self.ai_assist_button = IconButton("auto", "AI Assist")
            self.ai_assist_button.clicked.connect(self.ai_assist_requested.emit)
            button_layout.addWidget(self.ai_assist_button)

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
    """Widget for selecting between left and right eye annotations."""

    eye_changed = pyqtSignal(str)

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
            QRadioButton {
                color: #e0e0e0;
                spacing: 5px;
                padding: 5px;
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

        layout = QHBoxLayout()
        layout.setSpacing(10)

        self.left_eye_radio = QRadioButton("Left Eye")
        self.right_eye_radio = QRadioButton("Right Eye")
        self.left_eye_radio.setChecked(True)

        self.button_group = QButtonGroup()
        self.button_group.addButton(self.left_eye_radio)
        self.button_group.addButton(self.right_eye_radio)

        self.left_eye_radio.clicked.connect(lambda: self.eye_changed.emit("left"))
        self.right_eye_radio.clicked.connect(lambda: self.eye_changed.emit("right"))

        layout.addWidget(self.left_eye_radio)
        layout.addWidget(self.right_eye_radio)
        layout.addStretch()

        self.setLayout(layout)

    def get_current_eye(self) -> str:
        """Get the currently selected eye."""
        return "left" if self.left_eye_radio.isChecked() else "right"

    def set_current_eye(self, eye: str) -> None:
        """Set the currently selected eye."""
        if eye == "left":
            self.left_eye_radio.setChecked(True)
        else:
            self.right_eye_radio.setChecked(True)
