"""Shared non-fatal error reporting: show a dialog instead of crashing."""

from collections.abc import Iterator
from contextlib import contextmanager

from PyQt5.QtWidgets import QMessageBox, QWidget


def show_error(parent: QWidget | None, title: str, message: str) -> None:
    """Show a non-fatal warning dialog."""
    QMessageBox.warning(parent, title, message)


@contextmanager
def error_dialog(parent: QWidget | None, title: str, detail: str = "") -> Iterator[None]:
    """Run a block; on any exception show a dialog and continue instead of crashing.

    Put success-only side effects inside the ``with`` so they are skipped when the
    block raises.
    """
    try:
        yield
    except Exception as exc:
        show_error(parent, title, f"{detail}\n\n{exc}" if detail else str(exc))
