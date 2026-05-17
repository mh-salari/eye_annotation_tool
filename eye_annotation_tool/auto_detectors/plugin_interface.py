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

Optional panel surface for plugins whose detect step produces a binary
mask the user may want to overlay during tuning:

  - ``show_mask_toggled: pyqtSignal(bool)`` — emitted when the panel's
    "Show mask" checkbox flips. MainWindow forwards to the image viewer's
    per-target mask visibility flag.

If a plugin's ``detect`` returns a ``"mask"`` key in its result dict
(a uint8 ``numpy.ndarray``), MainWindow pushes the array to the viewer
under the plugin's ``target``; the viewer paints it as a semi-transparent
fill when the per-target "Show mask" flag is on. ``serialize`` must
always strip the mask — it is transient view-only state and never
belongs in the per-image JSON.
"""

from abc import ABC, abstractmethod
from typing import Literal

import numpy as np
from PyQt5.QtGui import QColor, QPainter
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

    # Z-order for the canvas overlay paint pass. Lower values are drawn
    # first (behind). Sensible defaults: limbus = -10 so its iris ring
    # sits under the pupil/glint markers; pupil = 0; glint = 10.
    overlay_z_order: int = 0

    # Optional colour for the per-target ROI rectangle the image viewer
    # draws when this plugin's panel exposes ``roi_edit_requested``.
    # Leave ``None`` for plugins that don't surface an ROI control.
    roi_color: QColor | None = None

    # Optional colour for the threshold-mask overlay the image viewer
    # paints when the user toggles this plugin's "Show mask" checkbox.
    # Set only on plugins whose ``detect`` returns a ``"mask"`` key.
    mask_color: QColor | None = None

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

    def draw_overlay(  # noqa: PLR6301 - subclasses override with stateful work
        self,
        painter: QPainter,  # noqa: ARG002 - default impl paints nothing
        result: dict,  # noqa: ARG002
        scale: float,  # noqa: ARG002
    ) -> None:
        """Render this plugin's detection geometry on the canvas.

        ``result`` is the in-memory dict ``deserialize`` produces (or
        ``detect`` returns). ``scale`` is the image-viewer zoom factor
        — multiply every image-space coordinate by it before painting.

        Default implementation paints nothing; plugins whose result is
        only consumed by downstream plugins (no on-screen geometry to
        show) can leave it as-is. Threshold-mask and per-target ROI
        rectangles are drawn separately by the image viewer using
        :attr:`mask_color` / :attr:`roi_color` — this method only needs
        to render the detection's own shape (e.g. pupil ellipse,
        glint dots, limbus circle).
        """
        return

    def translate_for_crop(  # noqa: PLR6301 - subclasses override with stateful work
        self,
        result: dict,
        dx: float,  # noqa: ARG002 - default impl returns the result unchanged
        dy: float,  # noqa: ARG002
    ) -> dict:
        """Translate a crop-coord result back into full-image coords.

        Called by MainWindow when this plugin was run on a cropped
        image (e.g. the active half of a binocular image). ``dx`` /
        ``dy`` are the offsets of the crop's top-left corner inside
        the full image. Implementations return a new result dict with
        every coordinate field shifted by ``(dx, dy)`` so downstream
        consumers + the viewer see full-image coordinates uniformly.

        The default implementation returns ``result`` unchanged — fine
        for plugins whose result carries no spatial fields, but
        plugins that report centres, ellipses, contours or glint
        points MUST override.

        ``"mask"`` arrays are NOT translated here. MainWindow embeds
        the crop-sized mask into a full-image-sized array before
        passing it to the viewer because the plugin doesn't know the
        full image shape.
        """
        return result

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
