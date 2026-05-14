"""Preferences dialog: per-project settings the user toggles at runtime."""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget


class PreferencesDialog(QDialog):
    """Per-project preferences dialog.

    Currently exposes one toggle (``Single eye mode``). The values are read
    from / written to the project settings file by the caller; this dialog
    only collects user input and emits a signal when the user accepts.
    """

    settings_changed = pyqtSignal(dict)

    def __init__(self, current_settings: dict, parent: QWidget | None = None) -> None:
        """Initialize with the current per-project settings dict."""
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(360)
        self._build_ui(current_settings)

    def _build_ui(self, current_settings: dict) -> None:
        layout = QVBoxLayout()

        header = QLabel("Per-project settings (saved next to your images).")
        header.setWordWrap(True)
        layout.addWidget(header)

        self.single_eye_checkbox = QCheckBox("Single eye mode")
        self.single_eye_checkbox.setToolTip(
            "When the image contains only one eye (e.g. pre-cropped per-eye images), "
            "hides the Left/Right eye selector and saves annotations in a flat format.",
        )
        self.single_eye_checkbox.setChecked(bool(current_settings.get("single_eye_mode", False)))
        layout.addWidget(self.single_eye_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def _on_accept(self) -> None:
        """Emit the updated settings dict and close."""
        self.settings_changed.emit({"single_eye_mode": self.single_eye_checkbox.isChecked()})
        self.accept()
