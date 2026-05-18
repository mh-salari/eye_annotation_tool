"""Main entry point for the eye annotation application."""

import argparse
import sys
from pathlib import Path

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .gui import MainWindow
from .gui.new_project_dialog import NewProjectDialog
from .utils.project_settings import (
    DEFAULT_DETECTOR_PLUGINS,
    DETECTOR_TARGETS,
    PROJECT_FILE_SUFFIX,
    default_project,
    load_project,
    save_project,
)

VALID_AUTO_DETECTOR_TARGETS = set(DETECTOR_TARGETS)


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
        "--project",
        type=Path,
        default=None,
        help=(
            f"Open an existing project file (*{PROJECT_FILE_SUFFIX}). If "
            f"combined with --images / --folders, those images are appended "
            f"to the project's working set."
        ),
    )
    parser.add_argument(
        "--new-project",
        type=Path,
        default=None,
        help=(
            "Create a new project file at this path with the per-target "
            "detector choices given via --pupil / --glint / --limbus / "
            "--eyelid (each defaulting to the same plugin the New Project "
            "wizard would pick). Mutually exclusive with --project."
        ),
    )
    parser.add_argument("--pupil", default=None, help="Plugin slug or 'none' for the pupil detector (used by --new-project).")
    parser.add_argument("--glint", default=None, help="Plugin slug or 'none' for the glint detector (used by --new-project).")
    parser.add_argument("--limbus", default=None, help="Plugin slug or 'none' for the limbus detector (used by --new-project).")
    parser.add_argument("--eyelid", default=None, help="Plugin slug or 'none' for the eyelid detector (used by --new-project).")
    parser.add_argument(
        "--autosave",
        action="store_true",
        help="Pre-set the autosave flag in a new project (used by --new-project).",
    )
    parser.add_argument(
        "--folders",
        type=Path,
        nargs="+",
        default=None,
        help=(
            "One or more folders whose images to add to the loaded project "
            "on startup (non-recursive). Used together with --project, "
            "--new-project, or stand-alone (creates an unsaved project)."
        ),
    )
    parser.add_argument(
        "--images",
        type=Path,
        nargs="+",
        default=None,
        help=(
            "Explicit list of image files to add to the loaded project on "
            "startup. Used together with --project, --new-project, or "
            "stand-alone (creates an unsaved project)."
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
    if args.project is not None and args.new_project is not None:
        parser.error("--project and --new-project are mutually exclusive.")
    if args.auto_detectors is not None:
        chosen = {t.strip().lower() for t in args.auto_detectors.split(",") if t.strip()}
        invalid = chosen - VALID_AUTO_DETECTOR_TARGETS
        if invalid:
            parser.error(
                f"unknown --auto-detectors target(s): {sorted(invalid)} "
                f"(choices: {sorted(VALID_AUTO_DETECTOR_TARGETS)})",
            )
        args.auto_detectors = chosen
    return args


class StartupChooserDialog(QDialog):
    """Modal: New Project / Open Project shown when the GUI launches with no CLI hints."""

    NEW = "new"
    OPEN = "open"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("EyE Annotation Tool")
        self.setMinimumWidth(360)
        self._choice: str | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Start a new project or open an existing one."))
        button_row = QHBoxLayout()
        new_button = QPushButton("New Project…")
        new_button.clicked.connect(self._on_new)
        open_button = QPushButton("Open Project…")
        open_button.clicked.connect(self._on_open)
        button_row.addWidget(new_button)
        button_row.addWidget(open_button)
        layout.addLayout(button_row)
        bbox = QDialogButtonBox(QDialogButtonBox.Cancel)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    def _on_new(self) -> None:
        self._choice = self.NEW
        self.accept()

    def _on_open(self) -> None:
        self._choice = self.OPEN
        self.accept()

    @property
    def choice(self) -> str | None:
        return self._choice


def _resolve_new_project_detectors(args: argparse.Namespace) -> dict:
    """Build the detectors block for a CLI-driven --new-project, honouring per-target flags."""
    detectors: dict = {}
    for target in DETECTOR_TARGETS:
        flag_value = getattr(args, target)
        if flag_value is None:
            plugin = DEFAULT_DETECTOR_PLUGINS[target]
        elif flag_value.lower() in ("none", "disabled", ""):
            plugin = "disabled"
        else:
            plugin = flag_value
        detectors[target] = {"plugin": plugin, "params": {"left": None, "right": None, "single": None}}
    return detectors


def _bootstrap_new_project(args: argparse.Namespace) -> None:
    """Write a fresh project file at ``args.new_project`` so the GUI can open it."""
    project = default_project()
    project["binocular_mode"] = not args.monocular
    project["autosave"] = bool(args.autosave)
    project["detectors"] = _resolve_new_project_detectors(args)
    save_project(args.new_project, project)


def run_app() -> None:
    """Run the eye annotation application."""
    args = _parse_args(sys.argv[1:])

    app = QApplication(sys.argv[:1])

    icon_path = str(Path(__file__).parent / "resources" / "app_icon.ico")
    app.setWindowIcon(QIcon(icon_path))

    main_window = MainWindow(cli_monocular=args.monocular, cli_auto_detectors=args.auto_detectors)
    main_window.show()

    if args.new_project is not None:
        _bootstrap_new_project(args)
        main_window.open_project(str(args.new_project))
    elif args.project is not None:
        if not args.project.exists():
            print(f"error: project file does not exist: {args.project}", file=sys.stderr)
            sys.exit(2)
        main_window.open_project(str(args.project))
    elif args.images is None and args.folders is None:
        # Fully unattended launch with no CLI hints — show the startup chooser.
        chooser = StartupChooserDialog()
        if chooser.exec_() == QDialog.Accepted:
            if chooser.choice == StartupChooserDialog.NEW:
                wizard = NewProjectDialog(main_window)
                if wizard.exec_() == QDialog.Accepted:
                    payload = wizard.result_payload()
                    main_window.new_project(payload["path"], payload["project"])
            elif chooser.choice == StartupChooserDialog.OPEN:
                main_window.on_open_project()

    if args.folders is not None:
        for folder in args.folders:
            main_window.add_images_from_folder(str(folder))
    if args.images is not None:
        main_window.add_images([str(p) for p in args.images])

    sys.exit(app.exec_())


if __name__ == "__main__":
    run_app()
