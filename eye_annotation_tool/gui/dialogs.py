"""Shared dialog helpers: error reporting and action confirmation."""

from collections.abc import Iterator
from contextlib import contextmanager

from PyQt5.QtWidgets import QMessageBox, QWidget


def show_error(parent: QWidget | None, title: str, message: str) -> None:
    """Show a non-fatal warning dialog."""
    QMessageBox.warning(parent, title, message)


def confirm(parent: QWidget | None, title: str, message: str) -> bool:
    """Ask the user to confirm an action; return True only if they accept."""
    reply = QMessageBox.question(parent, title, message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    return reply == QMessageBox.Yes


def overwrite_or_rename(parent: QWidget | None, path: str) -> str:
    """Warn that ``path`` exists; return 'overwrite', 'rename', or 'cancel'."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("File already exists")
    box.setText(f"A project already exists at:\n{path}")
    box.setInformativeText("Overwrite it, rename, or cancel?")
    overwrite_button = box.addButton("Overwrite", QMessageBox.DestructiveRole)
    rename_button = box.addButton("Rename…", QMessageBox.ActionRole)
    box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(rename_button)
    box.exec_()
    clicked = box.clickedButton()
    if clicked is overwrite_button:
        return "overwrite"
    if clicked is rename_button:
        return "rename"
    return "cancel"


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
