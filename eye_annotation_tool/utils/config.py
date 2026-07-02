"""Location of the app's per-user config directory."""

import os
from pathlib import Path


def config_dir() -> Path:
    """Return the app config directory (``$XDG_CONFIG_HOME`` or ``~/.config``, plus the app name)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return root / "eye_annotation_tool"
