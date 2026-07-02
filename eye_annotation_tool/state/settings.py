"""App-global user settings persisted under the user config directory.

Stored as JSON at ``$XDG_CONFIG_HOME/eye_annotation_tool/settings.json`` (falling
back to ``~/.config``), alongside the recent-projects list. App-wide preferences
(the theme, etc.) live here, not in any per-project file.
"""

import json
import logging
import os
from pathlib import Path

VALID_THEMES = ("system", "dark", "light")
_DEFAULT_THEME = "system"

logger = logging.getLogger(__name__)


def _store_path() -> Path:
    """Return the path to the settings JSON file."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return root / "eye_annotation_tool" / "settings.json"


def _load() -> dict:
    """Return the settings dict, or an empty dict when absent/unreadable."""
    path = _store_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("could not read settings file %s; falling back to defaults", path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def load_theme() -> str:
    """Return the saved theme preference: ``system``, ``dark``, or ``light``."""
    theme = _load().get("theme")
    return theme if theme in VALID_THEMES else _DEFAULT_THEME


def save_theme(theme: str) -> None:
    """Persist the theme preference for the next launch."""
    if theme not in VALID_THEMES:
        raise ValueError(f"unknown theme {theme!r}")
    data = _load()
    data["theme"] = theme
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
