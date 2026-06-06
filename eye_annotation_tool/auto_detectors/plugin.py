"""Plugin contract for eye-annotation-tool detectors.

A plugin is a ``DetectorPlugin`` instance: a function that detects one
anatomical target on a grayscale eye image, plus enough metadata to
render its parameter panel and to wire upstream results into its
``function`` call.

Built-in plugins come from every detector cheshm exposes (see
``plugin_loader.from_cheshm``). External plugins are loaded from
``~/.config/eye_annotation_tool/plugins/*.py`` and from any directories
listed in the ``EYE_ANNOTATION_PLUGINS`` env var (``os.pathsep``-separated).
See ``README.md`` in this folder for the authoring guide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

Kind = Literal["pupil", "glint", "limbus", "eyelid"]


@dataclass
class SettingSpec:
    """A single tunable parameter, ready to bind to a Qt widget.

    Fields mirror what the Qt panel builder needs:

    - ``name`` — keyword argument name passed to ``DetectorPlugin.function``.
    - ``type`` — one of ``int``, ``float``, ``bool``, ``choice``,
      ``optional_int``, ``optional_float``, ``roi``, ``any``.
    - ``default`` — initial value (``None`` for ``optional_*`` types
      that start disabled).
    - ``min`` / ``max`` — slider bounds for numeric types.
    - ``choices`` — list of strings for ``type='choice'``.
    - ``label`` / ``help`` — user-facing strings; ``label`` defaults to
      ``name`` title-cased.
    - ``hidden`` — skip in the UI (useful for wired inputs the
      orchestrator fills automatically).
    """

    name: str
    default: Any
    type: str = "float"
    min: float | int | None = None
    max: float | int | None = None
    choices: list[str] = field(default_factory=list)
    label: str = ""
    help: str = ""
    hidden: bool = False


@dataclass
class DetectorPlugin:
    """One detector plugin.

    The orchestrator calls ``function(image, *wired_args, **settings)``:

    - ``image`` — grayscale ``np.uint8`` (first positional arg, always).
    - ``wired_args`` — positional values pulled from upstream results
      based on ``wired_inputs`` (e.g. ``pupil_center``, ``pupil_radius``,
      ``pupil_ellipse``). Listed in the order the function expects them.
    - ``settings`` — keyword args, one per ``SettingSpec``.

    The function returns the detection result dict (target-specific
    shape, see the project README) or ``None`` on detection failure.
    """

    name: str
    kind: Kind
    function: Callable[..., dict | None]
    settings: list[SettingSpec] = field(default_factory=list)
    wired_inputs: list[str] = field(default_factory=list)
    overlays: list[tuple[str, str]] = field(default_factory=list)
    description: str = ""
    family: str = ""
