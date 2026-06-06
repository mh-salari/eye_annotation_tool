"""Main entry point for the eye annotation application."""

import argparse
import sys
from pathlib import Path

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .gui import MainWindow
from .gui.dialogs import confirm
from .gui.new_project_dialog import NewProjectDialog
from .gui.recent_project_row import RecentProjectRow
from .gui.theme import apply_theme, theme
from .state import recent_projects, settings
from .utils.project_settings import (
    DEFAULT_ID_BY_KIND,
    DETECTOR_OFF,
    KINDS,
    PROJECT_FILE_SUFFIX,
    default_project,
    disambiguated_labels,
    save_project,
)

VALID_AUTO_KINDS = set(KINDS)


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
            "Create a new project file at this path with the per-kind "
            "detector choices given via --pupil / --glint / --limbus / "
            "--eyelid (each defaulting to the same plugin the New Project "
            "wizard would pick). Mutually exclusive with --project."
        ),
    )
    parser.add_argument(
        "--pupil", default=None, help="Detector slug ('off' / 'manual' / cheshm id) for pupil (used by --new-project)."
    )
    parser.add_argument(
        "--glint", default=None, help="Detector slug ('off' / 'manual' / cheshm id) for glint (used by --new-project)."
    )
    parser.add_argument(
        "--limbus",
        default=None,
        help="Detector slug ('off' / 'manual' / cheshm id) for limbus (used by --new-project).",
    )
    parser.add_argument(
        "--eyelid",
        default=None,
        help="Detector slug ('off' / 'manual' / cheshm id) for eyelid (used by --new-project).",
    )
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
            "on startup. Used together with --project, --new-project, or "
            "stand-alone (creates an unsaved project). Add --recursive to "
            "also include images in subfolders."
        ),
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="With --folders, also add images found in subfolders (recursive walk).",
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
        "--review",
        type=Path,
        nargs="+",
        default=None,
        help=(
            "Open --project in read-only mode with this image list instead "
            "of the project's saved list. Settings (detector plugins, "
            "params, divider, autosave, binocular flag) are loaded as usual; "
            "session edits stay in memory and do NOT write back to the "
            "project file. Per-image annotation JSONs save next to their "
            "PNGs as always. Useful for re-annotating a small subset of "
            "images against an existing project's settings without "
            "mutating that project. Requires --project."
        ),
    )
    parser.add_argument(
        "--auto-detectors",
        type=str,
        default=None,
        help=(
            "Comma-separated subset of {pupil, glint, limbus, eyelid} to "
            "enable for this session; kinds not listed are forced to "
            "'disabled'. Overrides the per-project detector choices. "
            "Example: --auto-detectors pupil keeps only the pupil auto "
            "detector active."
        ),
    )
    args = parser.parse_args(argv)
    if args.project is not None and args.new_project is not None:
        parser.error("--project and --new-project are mutually exclusive.")
    if args.review is not None:
        if args.project is None:
            parser.error("--review requires --project.")
        if args.new_project is not None:
            parser.error("--review cannot be combined with --new-project.")
        if args.images is not None or args.folders is not None:
            parser.error("--review supplies the image list; --images and --folders cannot be used with it.")
    if args.auto_detectors is not None:
        chosen = {t.strip().lower() for t in args.auto_detectors.split(",") if t.strip()}
        invalid = chosen - VALID_AUTO_KINDS
        if invalid:
            parser.error(
                f"unknown --auto-detectors kind(s): {sorted(invalid)} (choices: {sorted(VALID_AUTO_KINDS)})",
            )
        args.auto_detectors = chosen
    return args


class StartupChooserDialog(QDialog):
    """Modal: recent projects + New / Open, shown when the GUI launches with no CLI hints."""

    NEW = "new"
    OPEN = "open"
    OPEN_RECENT = "open_recent"

    def __init__(self) -> None:
        """Lay out recent projects, the New / Open buttons, and Cancel."""
        super().__init__()
        self.setWindowTitle("EyE Annotation Tool")
        self.setMinimumWidth(420)
        self._choice: str | None = None
        self._selected_path: str | None = None
        layout = QVBoxLayout(self)

        button_row = QHBoxLayout()
        new_button = QPushButton("New Project…")
        new_button.clicked.connect(self._on_new)
        open_button = QPushButton("Open Project…")
        open_button.clicked.connect(self._on_open)
        button_row.addWidget(new_button)
        button_row.addWidget(open_button)
        layout.addLayout(button_row)

        layout.addSpacing(12)

        self._recent_label = QLabel("Recent projects:")
        self._recent_host = QWidget()
        self._recent_layout = QVBoxLayout(self._recent_host)
        self._recent_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._recent_label)
        layout.addWidget(self._recent_host)
        self._rebuild_recent()

        bbox = QDialogButtonBox(QDialogButtonBox.Cancel)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    def _rebuild_recent(self) -> None:
        """Repopulate the recent-project rows from disk; hide the section when empty."""
        while self._recent_layout.count():
            widget = self._recent_layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        paths = recent_projects.load()
        for path, label in zip(paths, disambiguated_labels(paths), strict=True):
            row = RecentProjectRow(path, label, Path(path).exists())
            row.open_requested.connect(self._on_open_recent)
            row.remove_requested.connect(self._on_remove_recent)
            self._recent_layout.addWidget(row)
        self._recent_label.setVisible(bool(paths))
        self._recent_host.setVisible(bool(paths))

    def _on_open_recent(self, path: str) -> None:
        self._choice = self.OPEN_RECENT
        self._selected_path = path
        self.accept()

    def _on_remove_recent(self, path: str) -> None:
        if confirm(
            self, "Remove from recent", "Remove this project from the recent list?\nThe project file is not deleted."
        ):
            recent_projects.remove(path)
            self._rebuild_recent()

    def _on_new(self) -> None:
        self._choice = self.NEW
        self.accept()

    def _on_open(self) -> None:
        self._choice = self.OPEN
        self.accept()

    @property
    def choice(self) -> str | None:
        """Selected mode (``NEW`` / ``OPEN`` / ``OPEN_RECENT``), or ``None`` if cancelled."""
        return self._choice

    @property
    def selected_path(self) -> str | None:
        """The recent project path chosen, when :attr:`choice` is ``OPEN_RECENT``."""
        return self._selected_path


def _resolve_new_project_detectors(args: argparse.Namespace) -> dict:
    """Build the detectors block for a CLI-driven --new-project, honouring per-kind flags."""
    detectors: dict = {}
    for kind in KINDS:
        flag_value = getattr(args, kind)
        if flag_value is None:
            slug = DEFAULT_ID_BY_KIND[kind]
        elif flag_value.lower() in {"none", "off", "disabled", ""}:
            slug = DETECTOR_OFF
        else:
            slug = flag_value
        detectors[kind] = {"id": slug, "params": {"left": None, "right": None, "single": None}}
    return detectors


def _bootstrap_new_project(args: argparse.Namespace) -> None:
    """Write a fresh project file at ``args.new_project`` so the GUI can open it."""
    project = default_project()
    project["binocular_mode"] = not args.monocular
    project["autosave"] = bool(args.autosave)
    project["detectors"] = _resolve_new_project_detectors(args)
    save_project(args.new_project, project)


def _run_startup_chooser(main_window: "MainWindow") -> None:
    """Show the New / Open chooser, looping until a project loads or the user cancels."""
    while main_window.project_store.path is None:
        chooser = StartupChooserDialog()
        if chooser.exec_() != QDialog.Accepted:
            return
        if chooser.choice == StartupChooserDialog.NEW:
            wizard = NewProjectDialog(main_window)
            if wizard.exec_() == QDialog.Accepted:
                payload = wizard.result_payload()
                main_window.new_project(payload["path"], payload["project"])
        elif chooser.choice == StartupChooserDialog.OPEN:
            main_window.on_open_project()
        elif chooser.choice == StartupChooserDialog.OPEN_RECENT and chooser.selected_path is not None:
            main_window.open_project(chooser.selected_path)


def run_app() -> None:
    """Run the eye annotation application."""
    args = _parse_args(sys.argv[1:])

    app = QApplication(sys.argv[:1])

    apply_theme(settings.load_theme())
    theme.watch_os(app)

    icon_path = str(Path(__file__).parent / "resources" / "app_icon.ico")
    app.setWindowIcon(QIcon(icon_path))

    main_window = MainWindow(cli_monocular=args.monocular, cli_auto_detectors=args.auto_detectors)
    main_window.show()

    if args.new_project is not None:
        _bootstrap_new_project(args)
        main_window.open_project(str(args.new_project))
    elif args.review is not None:
        # Read-only session: load --project's settings, override the image
        # list with --review's paths, suppress writes back to the project
        # file. The validator above guarantees --project is set and that
        # --images / --folders are absent.
        if not args.project.exists():
            print(f"error: project file does not exist: {args.project}", file=sys.stderr)
            sys.exit(2)
        main_window.open_project_for_review(
            str(args.project),
            [str(p) for p in args.review],
        )
    elif args.project is not None:
        if not args.project.exists():
            print(f"error: project file does not exist: {args.project}", file=sys.stderr)
            sys.exit(2)
        main_window.open_project(str(args.project))
    elif args.images is None and args.folders is None:
        _run_startup_chooser(main_window)

    if args.folders is not None:
        for folder in args.folders:
            main_window.add_images_from_folder(str(folder), recursive=args.recursive)
    if args.images is not None:
        main_window.add_images([str(p) for p in args.images])

    sys.exit(app.exec_())


if __name__ == "__main__":
    run_app()
