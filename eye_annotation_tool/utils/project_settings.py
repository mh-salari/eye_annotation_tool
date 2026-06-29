"""Single-file project persistence.

A project is one ``*.eye_annotation_project.json`` file at a path the
user chooses. It holds the working image set plus all annotation
settings, and is updated in place whenever the image set or settings
change.

Schema::

    {
      "images": {
        "/abs/path/img1.png": {},
        "/abs/path/img2.png": {"divider_x_norm": 0.47}
      },
      "binocular_mode": true,
      "divider_x_norm": 0.5,
      "autosave": false,
      "auto_detect_on_load": false,
      "detectors": {
        "pupil":  {"id": "<cheshm-id>" | "off" | "manual",
                    "params": {"left": {...}|null, "right": {...}|null, "single": {...}|null},
                    "pinned": [...],
                    "overlays": {"<detector-id>": {"<key>": {"show": bool, "color": "#rrggbb", ...}}}},
        "glint":  {...},
        "limbus": {...},
        "eyelid": {...}
      }
    }

Each image is keyed by its absolute path; the value is a dict of
optional per-image overrides. The only such override today is
``divider_x_norm``, which beats the project-wide default for that one
image. An empty ``{}`` means "no overrides, use project defaults".

Per-image annotation files still live as ``<image_stem>_annotation.json``
next to each image — the project file owns the image *set* and the
*settings*, not the per-image annotations.
"""

import json
from collections import Counter
from pathlib import Path

PROJECT_FILE_SUFFIX = ".eye_annotation_project.json"

# Detector kinds the project can configure. Order is the dependency
# order at run time: pupil first, then glint / limbus / eyelid which
# may consume the pupil result.
# "purkinje_iv" is the 4th Purkinje image (posterior-lens reflection).
KINDS = ("pupil", "glint", "limbus", "eyelid", "purkinje_iv")

# Sentinel slugs for the detector picker: "off" suppresses the kind
# entirely; "manual" routes it to manual click-to-place annotation.
# Anything else is the ``Detector.id`` of a discovered cheshm detector.
DETECTOR_OFF = "off"
DETECTOR_MANUAL = "manual"

# Default detector id per kind for a freshly created project. Pupil
# starts in Manual because the user usually places points by hand first
# and the auto detectors downstream consume the pupil result; eyelid
# follows the same convention. Glint and limbus stay off until the user
# opts in.
DEFAULT_ID_BY_KIND: dict[str, str] = {
    "pupil": DETECTOR_MANUAL,
    "glint": DETECTOR_OFF,
    "limbus": DETECTOR_OFF,
    "eyelid": DETECTOR_OFF,
    "purkinje_iv": DETECTOR_OFF,
}

# Kinds whose manual annotation is a few distinct corneal-reflection points,
# capped per project so a stray click can't add a spurious one.
MANUAL_POINT_CAP_KINDS = ("glint", "purkinje_iv")
DEFAULT_MANUAL_MAX_POINTS = 1

DEFAULT_DIVIDER_X_NORM = 0.5

# Per-eye param slots; "single" is used in monocular mode where the eye
# selector is hidden, "left" / "right" in binocular mode.
EYE_SLOTS = ("left", "right", "single")


def normalize_project_filename(path: str) -> str:
    """Ensure ``path`` ends with the project suffix, dropping an accidental ``.json``."""
    if not path or path.endswith(PROJECT_FILE_SUFFIX):
        return path
    return path.removesuffix(".json") + PROJECT_FILE_SUFFIX


def strip_project_suffix(path: str) -> str:
    """Remove the project suffix from ``path`` for display."""
    return path.removesuffix(PROJECT_FILE_SUFFIX)


def disambiguated_labels(paths: list[str]) -> list[str]:
    """Project display labels: file name, plus a parent-path tail where names collide."""
    names = [Path(p).name.removesuffix(PROJECT_FILE_SUFFIX) for p in paths]
    counts = Counter(names)
    labels = []
    for path, name in zip(paths, names, strict=True):
        if counts[name] == 1:
            labels.append(name)
        else:
            group = [other for other, n in zip(paths, names, strict=True) if n == name]
            labels.append(f"{name}  ({_distinguishing_tail(path, group)})")
    return labels


def _distinguishing_tail(path: str, group: list[str]) -> str:
    """Shortest trailing parent path of ``path`` that is unique within ``group``."""
    parts = Path(path).parent.parts
    others = [Path(other).parent.parts for other in group if other != path]
    for depth in range(1, len(parts) + 1):
        tail = parts[-depth:]
        if all(tail != other[-depth:] for other in others):
            return "/".join(tail)
    return str(Path(path).parent)


class ProjectSchemaError(RuntimeError):
    """Raised when a loaded project file uses an unsupported schema."""


def _default_params_per_eye() -> dict:
    """Return a fresh per-eye params block with every slot empty."""
    return dict.fromkeys(EYE_SLOTS)


def _default_detector_entry(kind: str) -> dict:
    """Fresh ``detectors.<kind>`` entry: the single source of per-kind defaults."""
    entry = {
        "id": DEFAULT_ID_BY_KIND[kind],
        "params": _default_params_per_eye(),
        "pinned": [],
        "overlays": {},
    }
    if kind in MANUAL_POINT_CAP_KINDS:
        entry["manual_max_points"] = DEFAULT_MANUAL_MAX_POINTS
    return entry


def default_project() -> dict:
    """Return a fresh deep dict of an empty project's defaults."""
    return {
        "images": {},
        "binocular_mode": True,
        "divider_x_norm": DEFAULT_DIVIDER_X_NORM,
        "autosave": False,
        "auto_detect_on_load": False,
        "detectors": {kind: _default_detector_entry(kind) for kind in KINDS},
    }


def load_project(project_path: str | Path) -> dict:
    """Load a project file from ``project_path`` and return a normalised payload.

    Raises :class:`ProjectSchemaError` on the older ``"plugin"`` or
    ``"detector"`` schemas — start a new project or recreate the
    detector picks via the side panel.
    """
    project = default_project()
    path = Path(project_path)
    if not path.exists():
        return project
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return project
    _reject_legacy_schema(loaded, path)
    detectors_in = loaded.pop("detectors", None)
    images_in = loaded.pop("images", None)
    project.update(loaded)
    if isinstance(images_in, dict):
        project["images"] = _parse_images(images_in)
    if isinstance(detectors_in, dict):
        for kind in KINDS:
            entry = detectors_in.get(kind)
            if isinstance(entry, dict):
                project["detectors"][kind] = _parse_detector_entry(kind, entry)
    return project


def save_project(project_path: str | Path, project: dict) -> None:
    """Write ``project`` to ``project_path``."""
    path = Path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")


def _reject_legacy_schema(loaded: dict, path: Path) -> None:
    """Raise :class:`ProjectSchemaError` if ``loaded`` is incompatible with the current schema."""
    detectors = loaded.get("detectors")
    if not isinstance(detectors, dict):
        return
    for entry in detectors.values():
        if not isinstance(entry, dict):
            continue
        if "id" in entry:
            continue
        if "plugin" in entry or "detector" in entry:
            raise ProjectSchemaError(f"Could not load project file {path}.")


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


def _parse_detector_entry(kind: str, entry: dict) -> dict:
    """Normalise a loaded ``detectors.<kind>`` block onto the per-kind defaults.

    Validated file values overlay :func:`_default_detector_entry`, so every
    default has a single source there rather than being respecified here.
    """
    out = _default_detector_entry(kind)
    if isinstance(entry.get("id"), str):
        out["id"] = entry["id"]
    out["params"] = _parse_params_per_eye(entry.get("params"))
    out["pinned"] = [name for name in (entry.get("pinned") or []) if isinstance(name, str)]
    out["overlays"] = _parse_overlays(entry.get("overlays"))
    raw_max = entry.get("manual_max_points")
    if kind in MANUAL_POINT_CAP_KINDS and isinstance(raw_max, int) and raw_max >= 1:
        out["manual_max_points"] = raw_max
    return out


def _parse_overlays(overlays_in: object) -> dict:
    """Normalise the nested overlay map: ``{detector_id: {overlay_key: fields}}``."""
    out: dict = {}
    if not isinstance(overlays_in, dict):
        return out
    for det_id, state in overlays_in.items():
        if isinstance(state, dict):
            out[det_id] = {k: dict(v) for k, v in state.items() if isinstance(v, dict)}
    return out


def _parse_params_per_eye(params_in: object) -> dict:
    """Normalise a per-eye ``params`` block; slots with non-dict values become ``None``."""
    out = _default_params_per_eye()
    if not isinstance(params_in, dict):
        return out
    for slot in EYE_SLOTS:
        slot_value = params_in.get(slot)
        if isinstance(slot_value, dict):
            out[slot] = dict(slot_value)
    return out
