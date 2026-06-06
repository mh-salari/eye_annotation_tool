"""A reusable collapsible section: a chevron + title header over a hideable body."""

import qtawesome as qta
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QFrame, QToolButton, QVBoxLayout, QWidget

from .theme import theme


class CollapsibleSection(QWidget):
    """A titled section whose body the user expands/collapses via the header."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, *, expanded: bool = False, parent: QWidget | None = None) -> None:
        """Build the section with ``title``, starting expanded or collapsed."""
        super().__init__(parent)
        self._expanded = expanded
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QToolButton()
        self._header.setText(title)
        self._header.setAutoRaise(True)
        self._header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._header.clicked.connect(self._on_clicked)
        outer.addWidget(self._header)

        self._body = QFrame()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(8, 0, 0, 0)
        self._body.setVisible(expanded)
        outer.addWidget(self._body)

        self._refresh_icon()
        theme.changed.connect(self._refresh_icon)

    def add_widget(self, widget: QWidget) -> None:
        """Append ``widget`` to the section body."""
        self._body_layout.addWidget(widget)

    def insert_widget(self, index: int, widget: QWidget) -> None:
        """Insert ``widget`` into the section body at ``index``."""
        self._body_layout.insertWidget(index, widget)

    def take_widget(self, widget: QWidget) -> None:
        """Detach ``widget`` from the section body without deleting it."""
        self._body_layout.removeWidget(widget)

    def clear(self) -> None:
        """Delete every widget in the section body."""
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def is_empty(self) -> bool:
        """Return True when the body holds no widgets."""
        return self._body_layout.count() == 0

    def is_expanded(self) -> bool:
        """Return whether the body is currently shown."""
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """Show or hide the body without emitting :attr:`toggled`."""
        self._expanded = expanded
        self._body.setVisible(expanded)
        self._refresh_icon()

    def _on_clicked(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._refresh_icon()
        self.toggled.emit(self._expanded)

    def _refresh_icon(self) -> None:
        name = "mdi6.chevron-down" if self._expanded else "mdi6.chevron-right"
        self._header.setIcon(qta.icon(name, color=theme.color("icon_muted")))
