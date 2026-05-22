"""Discover detector plugins from cheshm + user dirs.

Two discovery channels:

1. **cheshm bridge** — every detector exposed by
   ``cheshm.gui.registry.discover_detectors()`` is wrapped as a
   :class:`DetectorPlugin`. No user action required.
2. **User dirs** — ``.py`` files in
   ``~/.config/eye_annotation_tool/plugins/`` and in any directory
   listed in the ``EYE_ANNOTATION_PLUGINS`` env var (``os.pathsep``-
   separated). Each file declares one or more plugins via a
   module-level ``PLUGINS = [DetectorPlugin(...), ...]`` list.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

from cheshm.gui.registry import Setting as _CheshmSetting
from cheshm.gui.registry import discover_detectors as _discover_cheshm

from .plugin import DetectorPlugin, SettingSpec

logger = logging.getLogger(__name__)


def _cheshm_setting_to_spec(s: _CheshmSetting) -> SettingSpec:
    return SettingSpec(
        name=s.name,
        default=s.default,
        type=s.type,
        min=s.min,
        max=s.max,
        choices=list(s.choices),
        label=s.label,
        help=s.help,
        hidden=s.hidden,
    )


def from_cheshm() -> list[DetectorPlugin]:
    """Wrap every cheshm-exposed detector as a :class:`DetectorPlugin`."""
    out: list[DetectorPlugin] = []
    for d in _discover_cheshm():
        out.append(
            DetectorPlugin(
                name=d.id,
                kind=d.kind,
                function=d.function,
                settings=[_cheshm_setting_to_spec(s) for s in d.settings],
                wired_inputs=list(d.wired_inputs),
                overlays=list(d.overlays),
                description=d.description,
                family=d.family,
            )
        )
    return out


def _from_user_file(path: Path) -> list[DetectorPlugin]:
    spec = importlib.util.spec_from_file_location(f"_eye_annotation_user_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        logger.warning("could not load plugin %s (no spec)", path)
        return []
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        logger.warning("plugin %s raised on import: %s", path, exc)
        return []
    plugins = getattr(mod, "PLUGINS", None)
    if plugins is None:
        return []
    out: list[DetectorPlugin] = []
    for p in plugins:
        if isinstance(p, DetectorPlugin):
            out.append(p)
        else:
            logger.warning("plugin %s: entry %r is not a DetectorPlugin", path, p)
    return out


def _from_user_dir(dir_path: Path) -> list[DetectorPlugin]:
    if not dir_path.is_dir():
        return []
    out: list[DetectorPlugin] = []
    for py in sorted(dir_path.glob("*.py")):
        if py.name.startswith("_"):
            continue
        out.extend(_from_user_file(py))
    return out


def _user_plugin_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("EYE_ANNOTATION_PLUGINS")
    if env:
        dirs.extend(Path(d).expanduser() for d in env.split(os.pathsep) if d)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    dirs.append(config_root / "eye_annotation_tool" / "plugins")
    return dirs


def discover_plugins() -> list[DetectorPlugin]:
    """Return all plugins from cheshm + user dirs.

    Duplicate names within the same ``kind`` keep the last loaded copy
    (user dirs override cheshm bridges of the same name), so authors can
    shadow a built-in detector with a tuned variant of the same id.
    """
    plugins: list[DetectorPlugin] = from_cheshm()
    for d in _user_plugin_dirs():
        plugins.extend(_from_user_dir(d))

    deduped: dict[tuple[str, str], DetectorPlugin] = {}
    for p in plugins:
        deduped[p.kind, p.name] = p
    return list(deduped.values())
