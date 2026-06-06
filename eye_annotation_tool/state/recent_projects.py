"""Recent-projects list persisted under the user config directory.

Stored as a JSON array of absolute project-file paths, most-recent first, at
``$XDG_CONFIG_HOME/eye_annotation_tool/recent_projects.json`` (falling back to
``~/.config``), matching where the plugin loader looks for its config.
"""

import json
import os
from pathlib import Path

_MAX_RECENT = 10


def _store_path() -> Path:
    """Return the path to the recent-projects JSON file."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return root / "eye_annotation_tool" / "recent_projects.json"


def load() -> list[str]:
    """Return the recent project paths, most-recent first."""
    path = _store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(p) for p in data if isinstance(p, str)]


def add(project_path: str) -> None:
    """Promote ``project_path`` to the front of the list (deduped, capped at 10)."""
    p = str(Path(project_path).resolve())
    items = [x for x in load() if x != p]
    items.insert(0, p)
    _save(items[:_MAX_RECENT])


def remove(project_path: str) -> None:
    """Drop ``project_path`` from the recent list; the project file is untouched."""
    p = str(Path(project_path).resolve())
    _save([x for x in load() if x != p])


def _save(items: list[str]) -> None:
    """Write ``items`` to the recent-projects store, creating the config dir if needed."""
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
