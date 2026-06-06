"""New Project wizard: pick save path + initial detector / mode settings."""

from pathlib import Path

from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from eye_annotation_tool.auto_detectors.plugin_loader import discover_plugins

from ..utils.project_settings import (
    DEFAULT_ID_BY_KIND,
    DETECTOR_MANUAL,
    DETECTOR_OFF,
    KINDS,
    PROJECT_FILE_SUFFIX,
    default_project,
)
from .combo_utils import fit_combo_to_items
from .dialogs import overwrite_or_rename


class NewProjectDialog(QDialog):
    """Modal wizard for creating a new project.

    Collects the save path + mode (binocular/monocular) + per-kind
    detector choice + autosave flag, then surfaces the result via
    :meth:`result_payload`. The host main window calls
    :func:`main_window.new_project` with that payload to write the
    project file on disk and load it into the session.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the New Project dialog and discover available detectors."""
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.setMinimumWidth(520)
        self._detectors_by_kind: dict[str, list] = {t: [] for t in KINDS}
        for det in discover_plugins():
            self._detectors_by_kind.setdefault(det.kind, []).append(det)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Pick a save path for the project file and the initial annotation\n"
            "settings. Images can be added later via the Load buttons.",
        )
        layout.addWidget(intro)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText(f"e.g. ~/projects/my_session{PROJECT_FILE_SUFFIX}")
        # Pre-fill with a sensible default so the user can save without
        # picking a folder first.
        self._path_edit.setText(str(Path.home() / "Desktop" / f"untitled{PROJECT_FILE_SUFFIX}"))
        browse_button = QPushButton("Browse…")
        browse_button.clicked.connect(self._on_browse)
        path_row.addWidget(QLabel("Project file:"))
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(browse_button)
        layout.addLayout(path_row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._binocular_radio = QRadioButton("Binocular")
        self._binocular_radio.setChecked(True)
        self._monocular_radio = QRadioButton("Monocular")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._binocular_radio)
        self._mode_group.addButton(self._monocular_radio)
        mode_row.addWidget(self._binocular_radio)
        mode_row.addWidget(self._monocular_radio)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        detectors_form = QFormLayout()
        detectors_form.setSpacing(6)
        self._detector_combos: dict[str, QComboBox] = {}
        for kind in KINDS:
            combo = QComboBox()
            combo.addItem("Off", DETECTOR_OFF)
            combo.addItem("Manual", DETECTOR_MANUAL)
            for det in self._detectors_by_kind.get(kind, []):
                combo.addItem(det.name, det.name)
            default_slug = DEFAULT_ID_BY_KIND[kind]
            default_idx = combo.findData(default_slug)
            if default_idx >= 0:
                combo.setCurrentIndex(default_idx)
            fit_combo_to_items(combo)
            self._detector_combos[kind] = combo
            detectors_form.addRow(QLabel(f"{kind.capitalize()} detector:"), combo)
        layout.addLayout(detectors_form)

        self._autosave_checkbox = QCheckBox("Autosave on image change")
        self._autosave_checkbox.setChecked(False)
        layout.addWidget(self._autosave_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Project file",
            str(Path.home() / "Desktop" / f"untitled{PROJECT_FILE_SUFFIX}"),
            f"Project Files (*{PROJECT_FILE_SUFFIX})",
        )
        if path:
            if not path.endswith(PROJECT_FILE_SUFFIX):
                path += PROJECT_FILE_SUFFIX
            self._path_edit.setText(path)

    def _on_accept(self) -> None:
        path = self._path_edit.text().strip()
        if not path:
            QMessageBox.warning(
                self,
                "Project path required",
                "Pick a save path for the project file before creating it.",
            )
            return
        if not path.endswith(PROJECT_FILE_SUFFIX):
            path += PROJECT_FILE_SUFFIX
        path_obj = Path(path)
        parent = path_obj.parent
        if not parent.exists():
            QMessageBox.warning(
                self,
                "Parent folder missing",
                f"The folder {parent} does not exist. Pick a different path.",
            )
            return
        if path_obj.exists():
            choice = overwrite_or_rename(self, str(path_obj))
            if choice == "overwrite":
                self.accept()
            elif choice == "rename":
                self._on_browse()
            return
        self.accept()

    def result_payload(self) -> dict:
        """Return the ``{path, project}`` payload from the dialog's inputs."""
        path = self._path_edit.text().strip()
        if path and not path.endswith(PROJECT_FILE_SUFFIX):
            path += PROJECT_FILE_SUFFIX
        project = default_project()
        project["binocular_mode"] = self._binocular_radio.isChecked()
        project["autosave"] = self._autosave_checkbox.isChecked()
        detectors = project["detectors"]
        for kind, combo in self._detector_combos.items():
            slug = combo.currentData()
            block = detectors[kind]
            block["id"] = slug
            block["params"] = dict.fromkeys(("left", "right", "single"))
        return {"path": path, "project": project}
