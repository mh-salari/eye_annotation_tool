"""Right-side control panel.

Hosts the eye selector at the top and one :class:`DetectorCard` per
detector kind underneath. Each card owns its own picker (Off / Manual /
cheshm detector id), the active detector's settings, the overlay row,
and (for Manual) the per-kind manual annotation group widget. Clear
All sits at the bottom and dispatches to the image viewer.
"""

from cheshm.shape import CENTER_METHODS
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

from eye_annotation_tool.auto_detectors.plugin import DetectorPlugin as Detector

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
    "purkinje_iv": "purkinje_iv",
}


class AnnotationControlPanel(QWidget):
    """Right-panel widget hosting the eye selector and per-kind detector cards."""

    points_active_toggled = pyqtSignal(str, bool)
    delete_points_toggled = pyqtSignal(str, bool)
    max_points_changed = pyqtSignal(str, int)
    eye_changed = pyqtSignal(str)
    binocular_toggled = pyqtSignal(bool)
    fit_annotation_requested = pyqtSignal()
    clear_all_requested = pyqtSignal()
    clear_selected_annotation_requested = pyqtSignal()

    def __init__(
        self,
        detectors_by_kind: dict[str, list[Detector]],
        parent: QWidget | None = None,
    ) -> None:
        """Build the panel from the per-kind detector lists."""
        super().__init__(parent)
        self.setFixedWidth(360)
        self._detectors_by_kind = detectors_by_kind
        self.setup_ui()

    def setup_ui(self) -> None:
        """Build the eye selector, per-kind cards, and Clear All button."""
        layout = QVBoxLayout()

        self.eye_selector = EyeSelector()
        self.eye_selector.eye_changed.connect(self.eye_changed.emit)
        self.eye_selector.binocular_toggled.connect(self.binocular_toggled.emit)
        layout.addWidget(self.eye_selector)

        self.pupil_group = AnnotationGroup("Pupil", has_fit=True, center_methods=CENTER_METHODS)
        self.pupil_group.points_active_toggled.connect(lambda a: self.points_active_toggled.emit("pupil", a))
        self.pupil_group.delete_active_toggled.connect(lambda a: self.delete_points_toggled.emit("pupil", a))
        self.pupil_group.fit_requested.connect(self.fit_annotation_requested.emit)

        self.limbus_group = AnnotationGroup("Limbus", has_fit=True, center_methods=CENTER_METHODS)
        self.limbus_group.points_active_toggled.connect(lambda a: self.points_active_toggled.emit("limbus", a))
        self.limbus_group.delete_active_toggled.connect(lambda a: self.delete_points_toggled.emit("limbus", a))
        self.limbus_group.fit_requested.connect(self.fit_annotation_requested.emit)

        self.eyelid_group = AnnotationGroup("Eyelid Contour", has_fit=False)
        self.eyelid_group.points_active_toggled.connect(lambda a: self.points_active_toggled.emit("eyelid", a))
        self.eyelid_group.delete_active_toggled.connect(lambda a: self.delete_points_toggled.emit("eyelid", a))

        self.glint_group = AnnotationGroup("Glint", has_fit=False, point_cap=True)
        self.glint_group.points_active_toggled.connect(lambda a: self.points_active_toggled.emit("glint", a))
        self.glint_group.delete_active_toggled.connect(lambda a: self.delete_points_toggled.emit("glint", a))
        self.glint_group.max_points_changed.connect(lambda n: self.max_points_changed.emit("glint", n))

        self.purkinje_iv_group = AnnotationGroup("Purkinje IV", has_fit=False, point_cap=True)
        self.purkinje_iv_group.points_active_toggled.connect(lambda a: self.points_active_toggled.emit("purkinje_iv", a))
        self.purkinje_iv_group.delete_active_toggled.connect(lambda a: self.delete_points_toggled.emit("purkinje_iv", a))
        self.purkinje_iv_group.max_points_changed.connect(lambda n: self.max_points_changed.emit("purkinje_iv", n))

        self._manual_group_by_kind: dict[str, AnnotationGroup] = {
            "pupil": self.pupil_group,
            "limbus": self.limbus_group,
            "eyelid": self.eyelid_group,
            "glint": self.glint_group,
            "purkinje_iv": self.purkinje_iv_group,
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
        """Return the detector card for ``kind``, or ``None``."""
        return self.cards.get(kind)

    @staticmethod
    def manual_annotation_for_kind(kind: str) -> str | None:
        """Return the manual annotation slug for ``kind``, or ``None``."""
        return _MANUAL_ANNOTATION_BY_KIND.get(kind)

    def manual_group_for_kind(self, kind: str) -> AnnotationGroup | None:
        """Return the manual annotation group widget for ``kind``."""
        return self._manual_group_by_kind.get(kind)

    def active_points_kind(self) -> str | None:
        """Return the kind whose Add-points toggle is on, or ``None``."""
        for kind, group in self._manual_group_by_kind.items():
            if group.is_checked():
                return kind
        return None

    def get_current_eye(self) -> str:
        """Return the currently selected eye."""
        return self.eye_selector.get_current_eye()

    def set_current_eye(self, eye: str) -> None:
        """Select ``eye`` in the eye selector."""
        self.eye_selector.set_current_eye(eye)

    def is_binocular(self) -> bool:
        """Return whether binocular mode is active."""
        return self.eye_selector.is_binocular()

    def set_binocular(self, enabled: bool) -> None:
        """Toggle binocular mode."""
        self.eye_selector.set_binocular(enabled)
