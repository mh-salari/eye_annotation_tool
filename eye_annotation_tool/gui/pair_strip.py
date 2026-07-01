"""A strip shown while annotating an image that belongs to a compare pair.

Lets the user jump to the pair's other image or open the pair in Compare without
going to the pairs tree. The button for the image currently on screen is disabled.
"""

from pathlib import Path

import qtawesome as qta
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget

from .custom_widgets import MaterialButton
from .theme import theme


class PairStrip(QWidget):
    """Jump to A / B or open Compare for the pair the current image belongs to."""

    open_image_requested = pyqtSignal(str)
    open_compare_requested = pyqtSignal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the label + A / B / Compare buttons."""
        super().__init__(parent)
        self._path_a = ""
        self._path_b = ""
        box = QHBoxLayout(self)
        box.setContentsMargins(6, 4, 6, 4)
        box.addWidget(QLabel("Pair:"))
        self.a_button = MaterialButton("A", compact=True)
        self.a_button.clicked.connect(lambda: self.open_image_requested.emit(self._path_a))
        box.addWidget(self.a_button)
        self.b_button = MaterialButton("B", compact=True)
        self.b_button.clicked.connect(lambda: self.open_image_requested.emit(self._path_b))
        box.addWidget(self.b_button)
        self.compare_button = MaterialButton("Compare", compact=True)
        self.compare_button.setIcon(qta.icon("mdi6.compare-horizontal", color=theme.color("icon")))
        self.compare_button.clicked.connect(lambda: self.open_compare_requested.emit(self._path_a, self._path_b))
        box.addWidget(self.compare_button)
        box.addStretch(1)

    def set_pair(self, path_a: str, path_b: str, current: str) -> None:
        """Show the pair; the button for ``current`` (the loaded image) is disabled."""
        self._path_a = path_a
        self._path_b = path_b
        self.a_button.setToolTip(Path(path_a).name)
        self.b_button.setToolTip(Path(path_b).name)
        self.a_button.setEnabled(current != path_a)
        self.b_button.setEnabled(current != path_b)
