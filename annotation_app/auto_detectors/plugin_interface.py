"""Plugin contract for detector plugins.

A plugin is a self-contained module that owns:

  - the detection algorithm (``detect``),
  - its own Qt parameter panel (``make_panel``),
  - the default parameter values (``default_params``),
  - which other targets must be detected first (``requires``),
  - JSON ``serialize`` / ``deserialize`` for per-image persistence.

Core code (PluginManager, the orchestrator, the GUI mode switcher) is
plugin-agnostic — adding a new detector means dropping a new module under
``plugins/<target>_detectors/`` that implements this ABC. No edits to core
are needed.

A plugin's ``target`` is one of ``"pupil" | "glint" | "limbus" | "eyelid"``.
``name`` is a slug unique across all plugins (e.g. ``"threshold_pupil"``).
``requires`` lists the *targets* (not plugin names) whose results must be
computed before this plugin runs.

The panel widget returned by ``make_panel`` must expose:

  - ``params_changed: pyqtSignal(dict)`` — emitted on widget changes,
    payload is the new params dict,
  - ``current_params() -> dict`` — current widget state as params dict,
  - ``set_params(params: dict) -> None`` — populate widgets without
    emitting ``params_changed``.
"""

from abc import ABC, abstractmethod
from typing import Literal

import numpy as np
from PyQt5.QtWidgets import QWidget

Target = Literal["pupil", "glint", "limbus", "eyelid"]


class DetectorPlugin(ABC):
    """Base class for detector plugins. Subclasses set ``name``, ``target``, ``requires``, ``live``."""

    # Unique slug, e.g. ``"threshold_pupil"``. Used as the key in project
    # settings and per-image JSON; must match the value the user picks in the
    # auto-detector menu. Subclasses MUST override.
    name: str = ""

    # Which anatomical target this plugin produces a result for.
    target: Target = "pupil"

    # Targets whose results must be available in ``shared_results`` before
    # ``detect`` can run. Empty tuple means no dependencies. A plugin whose
    # dependency target has no enabled plugin (or has not yet run) will be
    # greyed out in the Auto Detect panel.
    requires: tuple[Target, ...] = ()

    # When ``True``, slider/parameter changes in the panel trigger a debounced
    # re-run of this plugin (reusing cached results for its dependencies).
    # When ``False``, the plugin only runs via the global "Run Auto Detect"
    # button — appropriate for slow algorithms (e.g. Daugman IDO limbus).
    live: bool = True

    @classmethod
    @abstractmethod
    def default_params(cls) -> dict:
        """Return a fresh dict of default parameter values for this plugin."""

    @abstractmethod
    def make_panel(self, parent: QWidget | None = None) -> QWidget:
        """Build and return the Qt parameter panel for this plugin.

        The returned widget owns its own state; the orchestrator connects
        to ``params_changed`` and pushes saved values via ``set_params``.
        """

    @abstractmethod
    def detect(
        self,
        image: np.ndarray,
        params: dict,
        shared_results: dict,
    ) -> dict | None:
        """Run detection on ``image`` with ``params``.

        ``shared_results`` maps target name → the latest result dict for
        that target (already serialized into the same shape ``deserialize``
        produces). Only keys listed in ``requires`` are guaranteed to be
        present; reading any other key is a programming error.

        Returns the detection result as a dict whose shape matches what
        ``serialize`` consumes, or ``None`` if the chosen method cannot
        produce a result at the current parameters.
        """

    @abstractmethod
    def serialize(self, result: dict) -> dict:
        """Reduce a result dict to JSON-friendly types for per-image storage.

        The returned dict is what gets written under
        ``per_image_json["detections"][self.name]["result"]``.
        """

    @abstractmethod
    def deserialize(self, blob: dict) -> dict:
        """Reconstruct the in-memory result dict from a stored JSON blob.

        Symmetric inverse of :meth:`serialize`. The reconstructed dict is
        what gets fed into other plugins' ``shared_results`` and into the
        viewer's overlay renderer.
        """
