"""Discovery and lookup for detector plugins.

:class:`PluginManager` collects plugins from three sources at startup
and indexes them by unique ``name`` slug and by ``target``:

1. The built-in directory ``plugins/<target>_detectors/*.py`` shipped
   inside this package.
2. Any directory listed in the ``EYE_ANNOTATION_PLUGIN_PATH`` env var
   (``os.pathsep``-separated, walked recursively for ``*.py`` files).
   Lets users drop a single ``my_pupil.py`` next to their work without
   building a pip-installable package.
3. Every Python entry point in the
   ``eye_annotation_tool.plugins`` group exposed by an installed
   distribution. Lets pip-installable plugins register themselves
   declaratively in their own ``pyproject.toml``.

Discovery is strict — a plugin file that does not yield a concrete
:class:`DetectorPlugin` subclass, or whose class fails to instantiate
(e.g. abstract methods not implemented), raises ``RuntimeError`` so
partially-migrated plugins are visible at boot rather than silently
skipped.
"""

import importlib.metadata
import importlib.util
import os
import sys
from pathlib import Path

from .plugin_interface import DetectorPlugin, Target

PLUGIN_PATH_ENV_VAR = "EYE_ANNOTATION_PLUGIN_PATH"
PLUGIN_ENTRY_POINT_GROUP = "eye_annotation_tool.plugins"


class PluginManager:
    """Load detector plugins from builtin / env-var / entry-points and index them."""

    PLUGINS_ROOT = Path(__file__).parent / "plugins"

    def __init__(self) -> None:
        """Discover and instantiate every plugin from each discovery channel."""
        self._by_name: dict[str, DetectorPlugin] = {}
        self._by_target: dict[Target, list[DetectorPlugin]] = {
            "pupil": [],
            "glint": [],
            "limbus": [],
            "eyelid": [],
        }
        self._discover_directory(self.PLUGINS_ROOT)
        self._discover_env_var()
        self._discover_entry_points()

    def _discover_directory(self, root: Path) -> None:
        """Walk ``root`` recursively and register every concrete subclass found."""
        if not root.exists():
            return
        for path in sorted(root.rglob("*.py")):
            if path.name.startswith("__"):
                continue
            module = self._import_path(path)
            for attr in vars(module).values():
                if isinstance(attr, type) and issubclass(attr, DetectorPlugin) and attr is not DetectorPlugin:
                    self._register(attr, str(path))

    def _discover_env_var(self) -> None:
        """Discover plugins from every directory in :data:`PLUGIN_PATH_ENV_VAR`."""
        raw = os.environ.get(PLUGIN_PATH_ENV_VAR, "")
        if not raw:
            return
        for entry in raw.split(os.pathsep):
            if not entry:
                continue
            root = Path(entry).expanduser().resolve()
            if not root.is_dir():
                raise RuntimeError(
                    f"{PLUGIN_PATH_ENV_VAR} entry {entry!r} is not an existing directory",
                )
            self._discover_directory(root)

    def _discover_entry_points(self) -> None:
        """Register every class advertised under the plugin entry-point group."""
        eps = importlib.metadata.entry_points(group=PLUGIN_ENTRY_POINT_GROUP)
        for ep in eps:
            cls = ep.load()
            if not isinstance(cls, type) or not issubclass(cls, DetectorPlugin) or cls is DetectorPlugin:
                raise RuntimeError(
                    f"entry point {ep.name!r} in group {PLUGIN_ENTRY_POINT_GROUP!r} "
                    f"resolved to {cls!r}, expected a DetectorPlugin subclass",
                )
            self._register(cls, f"<entry-point:{ep.name}>")

    @staticmethod
    def _import_path(path: Path) -> object:
        """Import a single plugin file as an isolated module."""
        # Each plugin module gets a unique sys.modules key so two plugins with
        # the same filename (e.g. two ``my_pupil.py`` files in different
        # external dirs) cannot shadow each other.
        slug = "".join(c if c.isalnum() else "_" for c in str(path.resolve()))
        module_name = f"_eat_plugin_{slug}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not build import spec for plugin {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def _register(self, plugin_cls: type[DetectorPlugin], source: str) -> None:
        """Instantiate ``plugin_cls`` and index it by name and target.

        ``source`` describes where the class came from (a file path for
        directory discovery, ``<entry-point:NAME>`` for entry-points) —
        it only flows into error messages so the user can find a broken
        plugin's origin.
        """
        plugin = plugin_cls()
        if not plugin.name:
            raise RuntimeError(f"plugin {plugin_cls!r} (from {source}) has empty .name")
        if plugin.name in self._by_name:
            other = type(self._by_name[plugin.name])
            raise RuntimeError(
                f"duplicate plugin name {plugin.name!r} (from {source}): clashes with {other!r}",
            )
        self._by_name[plugin.name] = plugin
        self._by_target[plugin.target].append(plugin)

    def get(self, name: str) -> DetectorPlugin | None:
        """Return the plugin registered under ``name``, or ``None`` if not loaded."""
        return self._by_name.get(name)

    def for_target(self, target: Target) -> list[DetectorPlugin]:
        """Return every plugin whose ``target`` matches ``target``."""
        return list(self._by_target.get(target, ()))

    def all(self) -> dict[str, DetectorPlugin]:
        """Return every loaded plugin keyed by name."""
        return dict(self._by_name)
