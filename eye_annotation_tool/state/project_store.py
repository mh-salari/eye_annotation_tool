"""In-memory project state with disk persistence.

Holds the on-disk ``*.eye_annotation_project.json`` dict, the active
file path, and the read-only session flag. Every mutation goes through
a named method so callers can write through to disk via :meth:`persist`
without each one re-implementing the "skip when no path or read-only"
gate.

Dialog flows (file pickers, missing-file warnings) live on
``MainWindow``; this class is pure state + I/O.
"""

from collections.abc import Callable
from pathlib import Path

from ..utils.project_settings import default_project, load_project, save_project


class ProjectStore:
    """Owns ``project: dict``, ``path: str | None``, and the read-only flag.

    Mutators call :meth:`persist` after every state change so the
    on-disk file stays in sync. Read-only sessions skip the write so
    edits stay session-local.
    """

    def __init__(self) -> None:
        """Start with an in-memory default project and no on-disk path."""
        self.project: dict = default_project()
        self.path: str | None = None
        self.read_only: bool = False
        self._on_error: Callable[[Exception], None] | None = None

    def set_error_handler(self, handler: Callable[[Exception], None]) -> None:
        """Register a callback invoked when a disk save fails (keeps this store GUI-agnostic)."""
        self._on_error = handler

    def _guarded_save(self) -> None:
        """Write the project to disk, routing a failure to the error handler."""
        try:
            save_project(self.path, self.project)
        except Exception as exc:
            if self._on_error is None:
                raise
            self._on_error(exc)

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    def new(self, project_path: str, initial: dict | None = None) -> None:
        """Create a fresh project at ``project_path`` and write it to disk.

        ``initial`` keys override the default-project fields (used by
        the New Project wizard to pre-fill detector choices, etc.).
        """
        self.project = default_project()
        if isinstance(initial, dict):
            for key, value in initial.items():
                self.project[key] = value
        self.path = str(project_path)
        self.read_only = False
        self._guarded_save()

    def load(self, project_path: str) -> None:
        """Replace the active project with the contents of ``project_path``."""
        self.project = load_project(project_path)
        self.path = str(project_path)
        self.read_only = False

    def load_for_review(self, project_path: str, image_paths: list[str]) -> None:
        """Load ``project_path`` read-only with the supplied image set.

        Settings are loaded as usual; only the images dict is replaced.
        Missing files in ``image_paths`` are dropped silently — the
        caller is responsible for surfacing them when needed. While
        :attr:`read_only` is True, :meth:`persist` is a no-op.
        """
        self.project = load_project(project_path)
        self.path = str(project_path)
        self.read_only = True
        valid = [str(Path(p)) for p in image_paths if Path(p).is_file()]
        self.project["images"] = {p: {} for p in valid}

    def save_as(self, project_path: str) -> None:
        """Write the project to ``project_path``; clears the read-only flag."""
        self.path = str(project_path)
        self.read_only = False
        self._guarded_save()

    def persist(self) -> None:
        """Write the project to disk if a path is set and we're not read-only."""
        if self.path is None or self.read_only:
            return
        self._guarded_save()

    # ---------------------------------------------------------------------------
    # Image set
    # ---------------------------------------------------------------------------

    def image_paths(self) -> list[str]:
        """Ordered list of image paths in the project."""
        return list(self.project["images"].keys())

    def has_image(self, path: str) -> bool:
        """Return whether ``path`` is already in the project's image dict."""
        return path in self.project["images"]

    def add_images(self, paths: list[str]) -> None:
        """Append ``paths`` to the project (already-present paths are skipped)."""
        for path in paths:
            self.project["images"].setdefault(path, {})
        self.persist()

    def remove_images(self, paths: list[str]) -> None:
        """Drop ``paths`` from the project's image dict (no-op for missing entries)."""
        for path in paths:
            self.project["images"].pop(path, None)
        self.persist()

    # ---------------------------------------------------------------------------
    # Per-image divider override
    # ---------------------------------------------------------------------------

    def divider_override(self, image_path: str) -> float | None:
        """Return the per-image divider override, or ``None`` if unset."""
        entry = self.project["images"].get(image_path)
        if not isinstance(entry, dict):
            return None
        value = entry.get("divider_x_norm")
        return float(value) if isinstance(value, (int, float)) else None

    def set_divider_override(self, image_path: str, value: float | None) -> None:
        """Set or clear the per-image divider override for ``image_path``."""
        entry = self.project["images"].setdefault(image_path, {})
        if value is None:
            entry.pop("divider_x_norm", None)
        else:
            entry["divider_x_norm"] = float(value)
        self.persist()

    # ---------------------------------------------------------------------------
    # Settings block accessors (everything below persists on write)
    # ---------------------------------------------------------------------------

    def detectors_block(self) -> dict:
        """Return ``project["detectors"]`` (live dict; mutate then call :meth:`persist`)."""
        return self.project.setdefault("detectors", {})

    def set_detectors_block(self, value: dict) -> None:
        """Replace the entire detectors block."""
        self.project["detectors"] = value
        self.persist()

    @property
    def binocular_mode(self) -> bool:
        """Project-wide binocular flag."""
        return bool(self.project.get("binocular_mode", True))

    @binocular_mode.setter
    def binocular_mode(self, value: bool) -> None:
        self.project["binocular_mode"] = bool(value)
        self.persist()

    @property
    def autosave(self) -> bool:
        """Autosave-on-image-change flag."""
        return bool(self.project.get("autosave", False))

    @autosave.setter
    def autosave(self, value: bool) -> None:
        self.project["autosave"] = bool(value)
        self.persist()

    @property
    def auto_detect_on_load(self) -> bool:
        """When True, opening an image runs the enabled auto-detectors live.

        When False the canvas shows only the saved-from-file detections and the
        user runs detection on demand via each detector card's Detect button.
        """
        return bool(self.project.get("auto_detect_on_load", False))

    @auto_detect_on_load.setter
    def auto_detect_on_load(self, value: bool) -> None:
        self.project["auto_detect_on_load"] = bool(value)
        self.persist()

    @property
    def divider_x_norm(self) -> float:
        """Project-wide default divider position (per-image overrides live elsewhere)."""
        return float(self.project.get("divider_x_norm", 0.5))

    @divider_x_norm.setter
    def divider_x_norm(self, value: float) -> None:
        self.project["divider_x_norm"] = float(value)
        self.persist()

    # ---------------------------------------------------------------------------
    # Misc
    # ---------------------------------------------------------------------------

    def project_root(self) -> Path | None:
        """Directory containing the project file, or ``None`` for unsaved projects."""
        return Path(self.path).parent if self.path else None
