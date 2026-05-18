"""Bounded undo history backed by :class:`collections.deque`.

Each entry is an opaque snapshot dict the caller decides how to
serialise/deserialise. The stack guards against unbounded growth via
``maxlen`` and discards future history on the first push after an
undo so the history reflects a single linear timeline.
"""

from collections import deque
from typing import Generic, TypeVar

State = TypeVar("State")


class UndoStack(Generic[State]):
    """Bounded linear undo history.

    The index points at the *current* state. :meth:`undo` decrements
    the index and returns the previous state; :meth:`push` after an
    undo discards everything beyond the new tip (the standard "branch
    abandonment" behaviour).
    """

    def __init__(self, maxlen: int = 10) -> None:
        """Initialise an empty stack with the given history bound."""
        self._maxlen: int = maxlen
        self._stack: deque[State] = deque(maxlen=maxlen)
        self._index: int = -1

    def reset(self, initial_state: State) -> None:
        """Drop history and seed the stack with ``initial_state``."""
        self._stack.clear()
        self._stack.append(initial_state)
        self._index = 0

    def push(self, state: State) -> None:
        """Record ``state`` as the new current entry.

        When the stack isn't at its tip (because of a previous undo),
        the post-index future is discarded — the new push replaces
        any redo branch.
        """
        if self._index < len(self._stack) - 1:
            self._stack = deque(list(self._stack)[: self._index + 1], maxlen=self._maxlen)
        self._stack.append(state)
        self._index = len(self._stack) - 1

    def can_undo(self) -> bool:
        """True when there's at least one earlier entry to step back to."""
        return self._index > 0

    def undo(self) -> State | None:
        """Step back one entry and return its state, or ``None`` when at the start."""
        if not self.can_undo():
            return None
        self._index -= 1
        return self._stack[self._index]

    def current(self) -> State | None:
        """Return the state at the current index, or ``None`` when the stack is empty."""
        if self._index < 0:
            return None
        return self._stack[self._index]
