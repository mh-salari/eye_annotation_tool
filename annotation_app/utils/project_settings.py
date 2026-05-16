"""Per-project settings persisted alongside the images.

Settings live in ``<project_dir>/.eye_annotation_project.json``. The project
directory is the folder of the currently loaded images. The file is
auto-created when a project-scoped setting is changed (e.g. a detector
plugin is picked, autosave is toggled) and auto-loaded the next time
images from that folder are opened.

Current schema::

    {
      "single_eye_mode": false,
      "autosave": false,
      "current_mode": "manual",
      "detectors": {
        "pupil":  {"plugin": "threshold_pupil" | "disabled", "params": {...}},
        "glint":  {"plugin": "disabled",                     "params": {}},
        "limbus": {"plugin": "disabled",                     "params": {}},
        "eyelid": {"plugin": "disabled",                     "params": {}}
      }
    }

Each per-target ``params`` block holds the defaults written by the
plugin's "Set as project defaults" action. Per-image overrides live in
the image annotation JSON, not here.
"""

import json
from pathlib import Path

PROJECT_SETTINGS_FILENAME = ".eye_annotation_project.json"

# Anatomical targets the project can configure a detector plugin for.
DETECTOR_TARGETS = ("pupil", "glint", "limbus", "eyelid")

DEFAULT_PROJECT_SETTINGS = {
    "single_eye_mode": False,
    "autosave": False,
    "current_mode": "manual",
    "detectors": {target: {"plugin": "disabled", "params": {}} for target in DETECTOR_TARGETS},
}


def project_settings_path(project_dir: str | Path) -> Path:
    """Return the path to the project settings file inside ``project_dir``."""
    return Path(project_dir) / PROJECT_SETTINGS_FILENAME


def _default_settings() -> dict:
    """Return a fresh deep copy of :data:`DEFAULT_PROJECT_SETTINGS`."""
    return {
        "single_eye_mode": False,
        "autosave": False,
        "current_mode": "manual",
        "detectors": {target: {"plugin": "disabled", "params": {}} for target in DETECTOR_TARGETS},
    }


def load_project_settings(project_dir: str | Path | None) -> dict:
    """Load project settings from ``project_dir``; return defaults if absent.

    Returns a fresh dict each time so the caller can mutate safely.
    Unknown top-level keys in the loaded file are preserved; missing
    keys are filled from defaults.
    """
    settings = _default_settings()
    if project_dir is None:
        return settings
    path = project_settings_path(project_dir)
    if not path.exists():
        return settings
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return settings
    # Merge top-level keys first, then merge the detectors sub-dict so a
    # file that only configures one target doesn't wipe the others.
    detectors_in = loaded.pop("detectors", None)
    settings.update(loaded)
    if isinstance(detectors_in, dict):
        for target in DETECTOR_TARGETS:
            entry = detectors_in.get(target)
            if isinstance(entry, dict):
                settings["detectors"][target] = {
                    "plugin": entry.get("plugin", "disabled"),
                    "params": dict(entry.get("params", {})),
                }
    return settings


def save_project_settings(project_dir: str | Path, settings: dict) -> None:
    """Write ``settings`` to the project settings file under ``project_dir``."""
    path = project_settings_path(project_dir)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
