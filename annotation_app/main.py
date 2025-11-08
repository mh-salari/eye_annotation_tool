"""Main entry point for the eye annotation application."""

import sys
from pathlib import Path

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from .gui import MainWindow


def run_app() -> None:
    """Run the eye annotation application."""
    app = QApplication(sys.argv)

    # Set the application icon
    icon_path = str(Path(__file__).parent / "resources" / "app_icon.ico")
    app.setWindowIcon(QIcon(icon_path))

    main_window = MainWindow()
    main_window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run_app()
