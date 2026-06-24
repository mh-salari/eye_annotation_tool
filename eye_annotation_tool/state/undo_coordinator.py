"""Application-level undo/redo across manual points and detector settings.

A single linear history of combined snapshots — each snapshot captures the
current image's manual annotation points *and* the active detector settings —
so one undo/redo gesture steps through both kinds of edit in the order they
happened. The history is seeded per image/eye via :meth:`reset`.

Two capture modes feed the history:

- :meth:`capture` records immediately (discrete edits, e.g. placing a point).
- :meth:`capture_debounced` waits out a short settle window so a continuous
  slider drag collapses into a single undo step instead of dozens.

Captures are deduplicated against the current tip, so an immediate capture and
a still-pending debounced capture describing the same state won't both land.
"""

from collections.abc import Callable

from PyQt5.QtCore import QObject, QTimer

from .undo_stack import UndoStack

SETTLE_MS = 400


class UndoCoordinator(QObject):
    """Shared undo/redo timeline driven by ``build_snapshot`` / ``apply_snapshot``."""

    def __init__(
        self,
        build_snapshot: Callable[[], dict],
        apply_snapshot: Callable[[dict], None],
        parent: QObject | None = None,
    ) -> None:
        """Wire the snapshot builder/applier and the settle timer."""
        super().__init__(parent)
        self._build = build_snapshot
        self._apply = apply_snapshot
        self._stack: UndoStack[dict] = UndoStack(maxlen=100)
        self._applying = False
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self.capture)

    def reset(self) -> None:
        """Drop history and seed it with the current state (call after an image/eye load)."""
        self._settle.stop()
        self._stack.reset(self._build())

    def capture(self) -> None:
        """Record the current state now, unless it matches the current tip."""
        if self._applying:
            return
        self._settle.stop()
        snapshot = self._build()
        if snapshot != self._stack.current():
            self._stack.push(snapshot)

    def capture_debounced(self) -> None:
        """Schedule a capture once edits settle (coalesces slider drags)."""
        if self._applying:
            return
        self._settle.start()

    def undo(self) -> None:
        """Step back one edit, flushing any pending debounced capture first."""
        if self._settle.isActive():
            self.capture()
        state = self._stack.undo()
        if state is not None:
            self._apply_state(state)

    def redo(self) -> None:
        """Step forward one edit."""
        state = self._stack.redo()
        if state is not None:
            self._apply_state(state)

    def _apply_state(self, state: dict) -> None:
        self._applying = True
        try:
            self._apply(state)
        finally:
            self._applying = False
