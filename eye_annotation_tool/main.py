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
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument(
        "--folders",
        type=Path,
        nargs="+",
        default=None,
        help=(
            "One or more folders to load on startup (same effect as clicking "
            "Load Folder, repeated). Each is walked (non-recursively) for "
            "supported images and the union is presented as a single sorted "
            "session. Per-project settings are taken from the first folder."
        ),
    )
    sources.add_argument(
        "--images",
        type=Path,
        nargs="+",
        default=None,
        help=(
            "Explicit list of image files to load on startup, in sorted order. "
            "Useful for narrow-scope launches that want to show only a curated "
            "set of images (e.g. a calibration helper). Mutually exclusive "
            "with --folders."
        ),
    )
    parser.add_argument(
        "--auto-detectors",
        type=str,
        default=None,
        help=(
            "Comma-separated subset of {pupil, glint, limbus, eyelid} to "
            "enable for this session; targets not listed are forced to "
            "'disabled'. Overrides the per-project detector choices. "
            "Example: --auto-detectors pupil keeps only the pupil auto "
            "detector active."
        ),
    )
    args = parser.parse_args(argv)
    if args.auto_detectors is not None:
        valid = {"pupil", "glint", "limbus", "eyelid"}
        chosen = {t.strip().lower() for t in args.auto_detectors.split(",") if t.strip()}
        invalid = chosen - valid
        if invalid:
            parser.error(
                f"unknown --auto-detectors target(s): {sorted(invalid)} "
                f"(choices: {sorted(valid)})",
            )
        args.auto_detectors = chosen
    return args


def run_app() -> None:
    """Run the eye annotation application."""
    args = _parse_args(sys.argv[1:])

    app = QApplication(sys.argv[:1])

    icon_path = str(Path(__file__).parent / "resources" / "app_icon.ico")
    app.setWindowIcon(QIcon(icon_path))

    main_window = MainWindow(cli_monocular=args.monocular, cli_auto_detectors=args.auto_detectors)
    main_window.show()
    if args.folders is not None:
        main_window.load_folder_paths([str(p) for p in args.folders])
    elif args.images is not None:
        main_window.load_image_paths([str(p) for p in args.images])
    sys.exit(app.exec_())


if __name__ == "__main__":
    run_app()
