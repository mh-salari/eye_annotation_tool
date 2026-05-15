"""Per-project settings persisted alongside the images.

Settings live in ``<project_dir>/.eye_annotation_project.json``. The project
directory is the folder of the currently loaded images. The file is
auto-created when a setting is toggled in the Preferences dialog or when an
auto-detector is selected, and auto-loaded the next time images from that
folder are opened.

Current schema::

    {
      "single_eye_mode": true,
      "current_mode": "annotate",
      "pupil_detector": "Threshold",
      "iris_detector": "disabled",
      "eyelid_detector": "disabled"
    }

Add new keys here as the GUI grows project-scoped flags.
"""

import json
from pathlib import Path

PROJECT_SETTINGS_FILENAME = ".eye_annotation_project.json"

# Keys mirrored from SettingsHandler (global auto-detector settings). When a
# project is loaded these per-project values take precedence; absence falls
# back to whatever the global file holds.
PROJECT_DETECTOR_KEYS = ("pupil_detector", "iris_detector", "eyelid_detector")

DEFAULT_PROJECT_SETTINGS = {
    "single_eye_mode": False,
    "current_mode": "annotate",
}


def project_settings_path(project_dir: str | Path) -> Path:
    """Return the path to the project settings file inside ``project_dir``."""
    return Path(project_dir) / PROJECT_SETTINGS_FILENAME


def load_project_settings(project_dir: str | Path | None) -> dict:
    """Load project settings from ``project_dir``; return defaults if absent.

    Returns a fresh dict each time so the caller can mutate safely.
    """
    if project_dir is None:
        return DEFAULT_PROJECT_SETTINGS.copy()
    path = project_settings_path(project_dir)
    if not path.exists():
        return DEFAULT_PROJECT_SETTINGS.copy()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return DEFAULT_PROJECT_SETTINGS.copy()
    settings = DEFAULT_PROJECT_SETTINGS.copy()
    settings.update(loaded)
    return settings


def save_project_settings(project_dir: str | Path, settings: dict) -> None:
    """Write ``settings`` to the project settings file under ``project_dir``."""
    path = project_settings_path(project_dir)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
