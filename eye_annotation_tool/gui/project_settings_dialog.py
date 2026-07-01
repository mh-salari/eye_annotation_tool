"""Dialog for editing project-wide settings (mode, autosave, per-kind detector picks)."""

from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from eye_annotation_tool.auto_detectors.plugin_loader import discover_plugins

from ..utils.project_settings import (
    DETECTOR_MANUAL,
    DETECTOR_OFF,
    KINDS,
)
from .combo_utils import fit_combo_to_items


class ProjectSettingsDialog(QDialog):
    """Edit binocular mode, autosave flag, and the per-kind detector picks."""

    def __init__(self, project: dict, parent: QWidget | None = None) -> None:
        """Build the Project Settings dialog for ``project``."""
        super().__init__(parent)
        self.setWindowTitle("Project Settings")
        self.setMinimumWidth(480)
        self._project = project
        self._detectors_by_kind: dict[str, list] = {k: [] for k in KINDS}
        for det in discover_plugins():
            self._detectors_by_kind.setdefault(det.kind, []).append(det)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Edit project-wide settings. Per-image detection results are unaffected."))

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._binocular_radio = QRadioButton("Binocular")
        self._monocular_radio = QRadioButton("Monocular")
        if bool(self._project.get("binocular_mode", True)):
            self._binocular_radio.setChecked(True)
        else:
            self._monocular_radio.setChecked(True)
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
        detectors_block = self._project.get("detectors") or {}
        for kind in KINDS:
            combo = QComboBox()
            combo.addItem("Off", DETECTOR_OFF)
            combo.addItem("Manual", DETECTOR_MANUAL)
            for det in self._detectors_by_kind.get(kind, []):
                combo.addItem(det.name, det.name)
            current = (detectors_block.get(kind) or {}).get("id", DETECTOR_OFF)
            current_idx = combo.findData(current)
            if current_idx >= 0:
                combo.setCurrentIndex(current_idx)
            fit_combo_to_items(combo)
            self._detector_combos[kind] = combo
            detectors_form.addRow(QLabel(f"{kind.capitalize()} detector:"), combo)
        layout.addLayout(detectors_form)

        self._autosave_checkbox = QCheckBox("Autosave on image change")
        self._autosave_checkbox.setChecked(bool(self._project.get("autosave", False)))
        layout.addWidget(self._autosave_checkbox)

        self._auto_detect_checkbox = QCheckBox("Auto-detect on image load")
        self._auto_detect_checkbox.setChecked(bool(self._project.get("auto_detect_on_load", False)))
        layout.addWidget(self._auto_detect_checkbox)

        self._enable_compare_checkbox = QCheckBox("Enable compare mode (image pairs)")
        self._enable_compare_checkbox.setChecked(bool(self._project.get("enable_compare", False)))
        layout.addWidget(self._enable_compare_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_payload(self) -> dict:
        """Return the edited fields the caller should merge into the project."""
        detectors: dict = {}
        existing = self._project.get("detectors") or {}
        for kind, combo in self._detector_combos.items():
            slug = combo.currentData()
            prev = existing.get(kind) or {}
            detectors[kind] = {
                "id": slug,
                "params": prev.get("params") or {"left": None, "right": None, "single": None},
            }
        return {
            "binocular_mode": self._binocular_radio.isChecked(),
            "autosave": self._autosave_checkbox.isChecked(),
            "auto_detect_on_load": self._auto_detect_checkbox.isChecked(),
            "enable_compare": self._enable_compare_checkbox.isChecked(),
            "detectors": detectors,
        }
