"""Application theme. PyQtDarkTheme provides the dark Material flat look."""

import qdarktheme
from PyQt5.QtWidgets import QApplication

# pyqtdarktheme paints a 2px accent underline on QCheckBox:hover /
# QRadioButton:hover. Override with the same selector pattern, keeping
# the transparent placeholder so the widget height stays stable.
_NO_HOVER_UNDERLINE = """
QCheckBox:hover, QRadioButton:hover {
    border-bottom: 2px solid transparent;
}
"""


def apply_theme(app: QApplication) -> None:
    """Apply the dark theme to ``app``."""
    qdarktheme.setup_theme(
        "dark",
        custom_colors={"primary": "#00897b"},
        additional_qss=_NO_HOVER_UNDERLINE,
    )
    _ = app  # qdarktheme operates on the currently-active QApplication
