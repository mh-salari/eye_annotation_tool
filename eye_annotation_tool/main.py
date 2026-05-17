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
        "--monocular",
        action="store_true",
        help=(
            "Force monocular mode for this session: treat the image as a "
            "single eye (no Left/Right split, no divider line, flat manual "
            "annotation schema). Overrides the per-project binocular setting."
        ),
    )
    parser.add_argument(
        "--folders",
        type=Path,
        nargs="+",
        default=None,
        help=(
            "One or more folders to load on startup (same effect as clicking "
            "Load Folder, repeated). Each is walked recursively for supported "
            "images and the union is presented as a single sorted session. "
            "Per-project settings are taken from the first folder."
        ),
    )
    return parser.parse_args(argv)


def run_app() -> None:
    """Run the eye annotation application."""
    args = _parse_args(sys.argv[1:])

    app = QApplication(sys.argv[:1])

    icon_path = str(Path(__file__).parent / "resources" / "app_icon.ico")
    app.setWindowIcon(QIcon(icon_path))

    main_window = MainWindow(cli_monocular=args.monocular)
    main_window.show()
    if args.folders is not None:
        main_window.load_folder_paths([str(p) for p in args.folders])
    sys.exit(app.exec_())


if __name__ == "__main__":
    run_app()
