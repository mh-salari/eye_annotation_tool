"""Per-session UI state: current image index + annotation dirty flag.

The two pieces of state don't belong to any single store — they
describe what the user is currently looking at and whether the
in-memory edit has been saved. Centralising them here lets
:class:`AnnotationController` and :class:`NavigationController`
depend on a named entity instead of three callable hooks each, and
lets MainWindow refresh the save-state indicator from one signal
instead of every mutator calling the refresh method directly.
"""

from PyQt5.QtCore import QObject, pyqtSignal


class SessionState(QObject):
    """Holds ``current_image_index`` and the annotation ``modified`` flag.

    The modified flag is a property so the :attr:`modified_changed`
    signal fires exactly once per real transition — MainWindow
    connects it to the save-state-indicator refresh slot so every
    place that toggles the flag stops having to call the refresh
    method explicitly.
    """

    modified_changed = pyqtSignal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        """Start with no image selected and a clean dirty flag."""
        super().__init__(parent)
        self.current_image_index: int = -1
        self._modified: bool = False

    @property
    def modified(self) -> bool:
        """True when the in-flight annotation has unsaved edits."""
        return self._modified

    @modified.setter
    def modified(self, value: bool) -> None:
        new_value = bool(value)
        if self._modified == new_value:
            return
        self._modified = new_value
        self.modified_changed.emit(new_value)
