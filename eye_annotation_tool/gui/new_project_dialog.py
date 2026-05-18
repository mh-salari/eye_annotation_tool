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

from ..auto_detectors.plugin_manager import PluginManager
from ..utils.project_settings import (
    DEFAULT_DETECTOR_PLUGINS,
    DETECTOR_TARGETS,
    PROJECT_FILE_SUFFIX,
    default_project,
)

DISABLED_LABEL = "disabled"


class NewProjectDialog(QDialog):
    """Modal wizard for creating a new project.

    Collects the save path + mode (binocular/monocular) + per-target
    detector plugin choice + autosave flag, then surfaces the result
    via :meth:`result_payload`. The host main window calls
    :func:`main_window.new_project` with that payload to write the
    project file on disk and load it into the session.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.setMinimumWidth(520)
        self._plugins = PluginManager()
        self._build_ui()

    def _build_ui(self) -> None:
        """Lay out the wizard fields."""
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Pick a save path for the project file and the initial annotation\n"
            "settings. Images can be added later via the Load buttons.",
        )
        layout.addWidget(intro)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText(f"e.g. ~/projects/my_session{PROJECT_FILE_SUFFIX}")
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
        for target in DETECTOR_TARGETS:
            combo = QComboBox()
            combo.addItem(DISABLED_LABEL, "disabled")
            for plugin in self._plugins.for_target(target):
                combo.addItem(plugin.name, plugin.name)
            default_slug = DEFAULT_DETECTOR_PLUGINS[target]
            default_idx = combo.findData(default_slug)
            if default_idx >= 0:
                combo.setCurrentIndex(default_idx)
            self._detector_combos[target] = combo
            detectors_form.addRow(QLabel(f"{target.capitalize()} detector:"), combo)
        layout.addLayout(detectors_form)

        self._autosave_checkbox = QCheckBox("Autosave on image change")
        self._autosave_checkbox.setChecked(False)
        layout.addWidget(self._autosave_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_browse(self) -> None:
        """Open a file-save dialog and write the chosen path into the line edit."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Project file",
            "",
            f"Project Files (*{PROJECT_FILE_SUFFIX})",
        )
        if path:
            if not path.endswith(PROJECT_FILE_SUFFIX):
                path = path + PROJECT_FILE_SUFFIX
            self._path_edit.setText(path)

    def _on_accept(self) -> None:
        """Validate the path before closing; reject with an explanation if missing."""
        path = self._path_edit.text().strip()
        if not path:
            QMessageBox.warning(
                self,
                "Project path required",
                "Pick a save path for the project file before creating it.",
            )
            return
        path_obj = Path(path)
        parent = path_obj.parent
        if not parent.exists():
            QMessageBox.warning(
                self,
                "Parent folder missing",
                f"The folder {parent} does not exist. Pick a different path.",
            )
            return
        self.accept()

    def result_payload(self) -> dict:
        """Return the wizard's chosen path + the project skeleton to feed ``new_project``."""
        path = self._path_edit.text().strip()
        if path and not path.endswith(PROJECT_FILE_SUFFIX):
            path = path + PROJECT_FILE_SUFFIX
        project = default_project()
        project["binocular_mode"] = self._binocular_radio.isChecked()
        project["autosave"] = self._autosave_checkbox.isChecked()
        detectors = project["detectors"]
        for target, combo in self._detector_combos.items():
            plugin_slug = combo.currentData()
            block = detectors[target]
            block["plugin"] = plugin_slug
            if plugin_slug != "disabled":
                plugin = self._plugins.get(plugin_slug)
                if plugin is not None:
                    block["params"] = {slot: plugin.default_params() for slot in ("left", "right", "single")}
        return {"path": path, "project": project}
