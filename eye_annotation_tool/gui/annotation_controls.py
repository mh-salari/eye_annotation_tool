"""Right-side control panel.

Hosts the eye selector at the top and one :class:`DetectorCard` per
detector kind underneath. Each card owns its own picker (Off / Manual /
cheshm detector id), the active detector's settings, the overlay row,
and (for Manual) the per-kind manual annotation group widget. Clear
All sits at the bottom and dispatches to the image viewer.
"""

from cheshm.gui.registry import Detector
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QVBoxLayout,
    QWidget,
)

from ..utils.project_settings import KINDS
from .custom_widgets import AnnotationGroup, EyeSelector, MaterialButton
from .detector_card import DetectorCard, build_detector_cards


# Annotation slug per detector kind — the canvas uses these names for the
# manual point / ellipse types it draws.
_MANUAL_ANNOTATION_BY_KIND = {
    "pupil": "pupil",
    "limbus": "limbus",
    "eyelid": "eyelid_contour",
    "glint": "glint",
}


class AnnotationControlPanel(QWidget):
    """Right-panel widget hosting the eye selector and per-kind detector cards."""

    annotation_changed = pyqtSignal(str)
    eye_changed = pyqtSignal(str)
    binocular_toggled = pyqtSignal(bool)
    fit_annotation_requested = pyqtSignal()
    clear_pupil_requested = pyqtSignal()
    clear_limbus_requested = pyqtSignal()
    clear_eyelid_points_requested = pyqtSignal()
    clear_glint_points_requested = pyqtSignal()
    clear_all_requested = pyqtSignal()
    clear_selected_annotation_requested = pyqtSignal()

    def __init__(
        self,
        detectors_by_kind: dict[str, list[Detector]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedWidth(360)
        self._detectors_by_kind = detectors_by_kind
        self.setup_ui()

    def setup_ui(self) -> None:
        layout = QVBoxLayout()

        self.eye_selector = EyeSelector()
        self.eye_selector.eye_changed.connect(self.eye_changed.emit)
        self.eye_selector.binocular_toggled.connect(self.binocular_toggled.emit)
        layout.addWidget(self.eye_selector)

        self.pupil_group = AnnotationGroup("Pupil", has_fit=True)
        self.pupil_group.selected.connect(lambda: self.annotation_changed.emit("pupil"))
        self.pupil_group.fit_requested.connect(self.fit_annotation_requested.emit)
        self.pupil_group.clear_requested.connect(self.clear_pupil_requested.emit)
        self.pupil_group.set_checked(True)

        self.limbus_group = AnnotationGroup("Limbus", has_fit=True)
        self.limbus_group.selected.connect(lambda: self.annotation_changed.emit("limbus"))
        self.limbus_group.fit_requested.connect(self.fit_annotation_requested.emit)
        self.limbus_group.clear_requested.connect(self.clear_limbus_requested.emit)

        self.eyelid_group = AnnotationGroup("Eyelid Contour", has_fit=False)
        self.eyelid_group.selected.connect(lambda: self.annotation_changed.emit("eyelid_contour"))
        self.eyelid_group.clear_requested.connect(self.clear_eyelid_points_requested.emit)

        self.glint_group = AnnotationGroup("Glint", has_fit=False)
        self.glint_group.selected.connect(lambda: self.annotation_changed.emit("glint"))
        self.glint_group.clear_requested.connect(self.clear_glint_points_requested.emit)

        # Radio-button group ties the per-card AnnotationGroup radios
        # together so only one annotation type is the click target at a time.
        self._radio_group = QButtonGroup(self)
        for group in (self.pupil_group, self.limbus_group, self.eyelid_group, self.glint_group):
            self._radio_group.addButton(group.radio)

        self._manual_group_by_kind: dict[str, AnnotationGroup] = {
            "pupil": self.pupil_group,
            "limbus": self.limbus_group,
            "eyelid": self.eyelid_group,
            "glint": self.glint_group,
        }

        self.cards: dict[str, DetectorCard] = build_detector_cards(
            self._detectors_by_kind,
            list(KINDS),
        )
        for kind in KINDS:
            card = self.cards[kind]
            manual_widget = self._manual_group_by_kind.get(kind)
            if manual_widget is not None:
                card.set_manual_host(manual_widget)
            layout.addWidget(card)

        layout.addStretch(1)

        self.clear_all_button = MaterialButton("Clear All")
        self.clear_all_button.clicked.connect(self.clear_all_requested.emit)

        self.setLayout(layout)

    # ----- accessors used by the controllers + main window -----

    def card(self, kind: str) -> DetectorCard | None:
        return self.cards.get(kind)

    def manual_annotation_for_kind(self, kind: str) -> str | None:
        return _MANUAL_ANNOTATION_BY_KIND.get(kind)

    def manual_group_for_kind(self, kind: str) -> AnnotationGroup | None:
        return self._manual_group_by_kind.get(kind)

    def set_current_annotation(self, annotation_type: str) -> None:
        """Tick the radio for ``annotation_type`` (pupil/limbus/eyelid_contour/glint)."""
        if annotation_type == "pupil":
            self.pupil_group.set_checked(True)
        elif annotation_type == "limbus":
            self.limbus_group.set_checked(True)
        elif annotation_type == "eyelid_contour":
            self.eyelid_group.set_checked(True)
        else:
            self.glint_group.set_checked(True)

    def get_current_annotation_type(self) -> str:
        """Return the radio-selected annotation type slug."""
        if self.pupil_group.is_checked():
            return "pupil"
        if self.limbus_group.is_checked():
            return "limbus"
        if self.eyelid_group.is_checked():
            return "eyelid_contour"
        return "glint"

    def get_current_eye(self) -> str:
        return self.eye_selector.get_current_eye()

    def set_current_eye(self, eye: str) -> None:
        self.eye_selector.set_current_eye(eye)

    def is_binocular(self) -> bool:
        return self.eye_selector.is_binocular()

    def set_binocular(self, enabled: bool) -> None:
        self.eye_selector.set_binocular(enabled)
