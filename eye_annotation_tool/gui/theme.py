"""Application theme: design tokens, light/dark palettes, and live switching.

``qdarktheme`` styles the standard Qt chrome (backgrounds, text, buttons) for the
chosen mode. The :data:`theme` singleton below owns the few custom colours that
qdarktheme doesn't cover — the qta icon greys, hover backgrounds, muted text,
links — as named tokens with a light and a dark value. Widgets read tokens via
``theme.color(...)`` and re-apply them on the :attr:`_Theme.changed` signal so a
theme switch takes effect live.
"""

import darkdetect
import qdarktheme
from PyQt5.QtCore import QEvent, QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication

PRIMARY = "#00897b"

# pyqtdarktheme paints a 2px accent underline on QCheckBox:hover /
# QRadioButton:hover. Override it back to a transparent placeholder so the
# widget height stays stable.
_NO_HOVER_UNDERLINE = """
QCheckBox:hover, QRadioButton:hover {
    border-bottom: 2px solid transparent;
}
"""

# Custom chrome colours not covered by qdarktheme, one set per resolved theme.
# Annotation overlay colours (pupil/glint/...) are NOT here: they ride on the eye
# image and stay the same in both themes (see canvas_renderer).
_TOKENS = {
    "dark": {
        "icon": "#e0e0e0",
        "icon_muted": "#9aa0a6",
        "icon_subtle": "#c0c0c0",
        "hover_bg": "rgba(255, 255, 255, 0.08)",
        "muted_fg": "#777777",
        "link": "#8b7aa2",
        "danger": "#e06c75",
        "danger_bg": "rgba(224, 108, 117, 0.22)",
    },
    "light": {
        "icon": "#3c3c3c",
        "icon_muted": "#6b6b6b",
        "icon_subtle": "#8a8a8a",
        "hover_bg": "rgba(0, 0, 0, 0.06)",
        "muted_fg": "#9a9a9a",
        "link": "#6a4fb0",
        "danger": "#c0392b",
        "danger_bg": "rgba(192, 57, 43, 0.12)",
    },
}


class _Theme(QObject):
    """Owns the active theme + custom token colours; emits :attr:`changed` on switch."""

    changed = pyqtSignal()

    def __init__(self) -> None:
        """Start in system mode, resolved to dark until :meth:`apply` runs."""
        super().__init__()
        self._mode = "system"  # user preference: system | dark | light
        self._resolved = "dark"  # actually applied: dark | light

    @property
    def mode(self) -> str:
        """The user's theme preference (``system`` / ``dark`` / ``light``)."""
        return self._mode

    @property
    def resolved(self) -> str:
        """The theme actually applied (``dark`` / ``light``)."""
        return self._resolved

    def color(self, token: str) -> str:
        """Return the colour string for ``token`` in the active theme."""
        return _TOKENS[self._resolved][token]

    def apply(self, mode: str) -> None:
        """Apply ``mode``, restyle the Qt chrome, and notify listeners."""
        self._mode = mode
        self._resolved = self._resolve(mode)
        qdarktheme.setup_theme(self._resolved, custom_colors={"primary": PRIMARY}, additional_qss=_NO_HOVER_UNDERLINE)
        self.changed.emit()

    def watch_os(self, app: QApplication) -> None:
        """Follow live OS appearance changes while in ``system`` mode."""
        app.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        """Re-apply on an OS appearance change when in ``system`` mode (loop-guarded)."""
        if (
            event.type() == QEvent.ApplicationPaletteChange
            and self._mode == "system"
            and self._resolve("system") != self._resolved
        ):
            self.apply("system")
        return super().eventFilter(obj, event)

    @staticmethod
    def _resolve(mode: str) -> str:
        """Map a preference to an actual theme, reading the OS for ``system``."""
        if mode in {"dark", "light"}:
            return mode
        detected = (darkdetect.theme() or "dark").lower()
        return detected if detected in {"dark", "light"} else "dark"


theme = _Theme()


def apply_theme(mode: str = "system") -> None:
    """Apply the theme for the application at startup."""
    theme.apply(mode)
