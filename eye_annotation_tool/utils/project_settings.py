"""Single-file project persistence.

A project is one ``*.eye_annotation_project.json`` file at a path the user
chooses. It holds the working image set plus all annotation settings, and is
updated in place whenever the image set or settings change.

Schema::

    {
      "images": {
        "/abs/path/img1.png": {},
        "/abs/path/img2.png": {"divider_x_norm": 0.47}
      },
      "binocular_mode": true,
      "divider_x_norm": 0.5,
      "autosave": false,
      "current_mode": "manual",
      "detectors": {
        "pupil":  {"plugin": "threshold_pupil" | "disabled",
                    "params": {"left": {...}|null, "right": {...}|null, "single": {...}|null},
                    "carry_roi": {"enabled": {...}, "values": {...}}},
        "glint":  {...},
        "limbus": {...},
        "eyelid": {...}
      }
    }

Each image is keyed by its absolute path; the value is a dict of optional
per-image overrides. Right now the only such override is ``divider_x_norm``,
which beats the project-wide default for that one image. An empty ``{}`` means
"no overrides, use project defaults".

Per-image annotation files still live as ``<image_stem>_annotation.json`` next to
each image — the project file owns the image *set* and the *settings*, not the
per-image annotations.
"""

import json
from pathlib import Path

PROJECT_FILE_SUFFIX = ".eye_annotation_project.json"

# Anatomical targets the project can configure a detector plugin for.
DETECTOR_TARGETS = ("pupil", "glint", "limbus", "eyelid")

# Default plugin slug per target. ``"disabled"`` means the target is off for
# this project. Pupil + glint + limbus all default to enabled — pupil is
# needed for any downstream target, glint depends on the pupil result, and
# limbus is opt-out for use cases that need an iris circle.
DEFAULT_DETECTOR_PLUGINS: dict[str, str] = {
    "pupil": "threshold_pupil",
    "glint": "threshold_glint",
    "limbus": "daugman_limbus",
    "eyelid": "disabled",
}

DEFAULT_DIVIDER_X_NORM = 0.5

# Per-eye slots the carry-over ROI store keeps; "single" is used in
# monocular mode where the eye selector is hidden, "left" / "right"
# in binocular mode.
CARRY_ROI_SLOTS = ("left", "right", "single")


def _default_carry_roi() -> dict:
    """Return a fresh carry-over block with every slot's gate off and no stored rect."""
    return {
        "enabled": dict.fromkeys(CARRY_ROI_SLOTS, False),
        "values": dict.fromkeys(CARRY_ROI_SLOTS),
    }


def _default_params_per_eye() -> dict:
    """Return a fresh per-eye params block with every slot empty."""
    return dict.fromkeys(CARRY_ROI_SLOTS)


def default_project() -> dict:
    """Return a fresh deep dict of an empty project's defaults."""
    return {
        "images": {},
        "binocular_mode": True,
        "divider_x_norm": DEFAULT_DIVIDER_X_NORM,
        "autosave": False,
        "current_mode": "manual",
        "detectors": {
            target: {
                "plugin": DEFAULT_DETECTOR_PLUGINS[target],
                "params": _default_params_per_eye(),
                "carry_roi": _default_carry_roi(),
            }
            for target in DETECTOR_TARGETS
        },
    }


def load_project(project_path: str | Path) -> dict:
    """Load a project file from ``project_path`` and return a normalised payload.

    Missing or unreadable files return :func:`default_project`. Unknown top-level
    keys in the loaded file are preserved; missing keys are filled from defaults.
    """
    project = default_project()
    path = Path(project_path)
    if not path.exists():
        return project
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return project
    detectors_in = loaded.pop("detectors", None)
    images_in = loaded.pop("images", None)
    project.update(loaded)
    if isinstance(images_in, dict):
        project["images"] = _parse_images(images_in)
    if isinstance(detectors_in, dict):
        for target in DETECTOR_TARGETS:
            entry = detectors_in.get(target)
            if isinstance(entry, dict):
                project["detectors"][target] = _parse_detector_entry(entry)
    return project


def save_project(project_path: str | Path, project: dict) -> None:
    """Write ``project`` to ``project_path``."""
    path = Path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")


def _parse_images(images_in: dict) -> dict:
    """Normalise the ``images`` map: keep absolute-path keys, well-formed value dicts."""
    out: dict[str, dict] = {}
    for key, value in images_in.items():
        if not isinstance(key, str):
            continue
        per_image: dict = {}
        if isinstance(value, dict):
            divider = value.get("divider_x_norm")
            if isinstance(divider, (int, float)):
                per_image["divider_x_norm"] = float(divider)
        out[key] = per_image
    return out


def _parse_detector_entry(entry: dict) -> dict:
    """Normalise one ``detectors.<target>`` block from disk into the in-memory shape."""
    return {
        "plugin": entry.get("plugin", "disabled"),
        "params": _parse_params_per_eye(entry.get("params")),
        "carry_roi": _parse_carry_roi(entry.get("carry_roi")),
    }


def _parse_params_per_eye(params_in: object) -> dict:
    """Normalise a per-eye ``params`` block; slots with non-dict values become ``None``."""
    out = _default_params_per_eye()
    if not isinstance(params_in, dict):
        return out
    for slot in CARRY_ROI_SLOTS:
        slot_value = params_in.get(slot)
        if isinstance(slot_value, dict):
            out[slot] = dict(slot_value)
    return out


def _parse_carry_roi(carry_in: object) -> dict:
    """Normalise a stored ``carry_roi`` block, dropping any malformed values."""
    carry = _default_carry_roi()
    if not isinstance(carry_in, dict):
        return carry
    enabled_in = carry_in.get("enabled")
    if isinstance(enabled_in, dict):
        for slot in CARRY_ROI_SLOTS:
            carry["enabled"][slot] = bool(enabled_in.get(slot, False))
    values_in = carry_in.get("values") or {}
    if isinstance(values_in, dict):
        for slot in CARRY_ROI_SLOTS:
            v = values_in.get(slot)
            carry["values"][slot] = (
                tuple(int(c) for c in v) if isinstance(v, (list, tuple)) and len(v) == 4 else None
            )
    return carry
