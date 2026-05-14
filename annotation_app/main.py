"""Main entry point for the eye annotation application."""

import argparse
import sys
from pathlib import Path

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from .gui import MainWindow


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the GUI CLI flags. Kept minimal — most settings live in the GUI."""
    parser = argparse.ArgumentParser(prog="eye_annotation_tool")
    parser.add_argument(
        "--single-eye",
        action="store_true",
        help=(
            "Force single-eye mode for this session: hide the Left/Right "
            "eye selector and save annotations in a flat schema. Overrides "
            "the per-project setting. Equivalent to enabling the same "
            "checkbox in File > Preferences."
        ),
    )
    return parser.parse_args(argv)


def run_app() -> None:
    """Run the eye annotation application."""
    args = _parse_args(sys.argv[1:])

    app = QApplication(sys.argv[:1])

    icon_path = str(Path(__file__).parent / "resources" / "app_icon.ico")
    app.setWindowIcon(QIcon(icon_path))

    main_window = MainWindow(cli_single_eye=args.single_eye)
    main_window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run_app()
