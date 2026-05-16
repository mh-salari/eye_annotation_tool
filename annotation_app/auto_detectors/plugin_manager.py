"""Discovery and lookup for detector plugins.

:class:`PluginManager` walks ``plugins/<target>_detectors/*.py`` at startup,
instantiates every subclass of :class:`DetectorPlugin`, and indexes them by
unique ``name`` slug and by ``target``. Discovery is strict — a plugin file
that does not yield a concrete ``DetectorPlugin`` subclass, or whose class
fails to instantiate (e.g. abstract methods not implemented), raises
``RuntimeError`` so partially-migrated plugins are visible at boot rather
than silently skipped.
"""

import importlib.util
import sys
from pathlib import Path

from .plugin_interface import DetectorPlugin, Target


class PluginManager:
    """Load detector plugins from ``plugins/`` and index them by name and target."""

    PLUGINS_ROOT = Path(__file__).parent / "plugins"

    def __init__(self) -> None:
        """Discover and instantiate every plugin under :attr:`PLUGINS_ROOT`."""
        self._by_name: dict[str, DetectorPlugin] = {}
        self._by_target: dict[Target, list[DetectorPlugin]] = {
            "pupil": [],
            "glint": [],
            "limbus": [],
            "eyelid": [],
        }
        self._discover()

    def _discover(self) -> None:
        """Walk :attr:`PLUGINS_ROOT` and register every concrete subclass."""
        for path in sorted(self.PLUGINS_ROOT.rglob("*.py")):
            if path.name.startswith("__"):
                continue
            module = self._import_path(path)
            for attr in vars(module).values():
                if isinstance(attr, type) and issubclass(attr, DetectorPlugin) and attr is not DetectorPlugin:
                    self._register(attr, path)

    @staticmethod
    def _import_path(path: Path) -> object:
        """Import a single plugin file as an isolated module."""
        # Each plugin module gets a unique sys.modules key so two plugins with
        # the same filename in different target dirs cannot shadow each other.
        module_name = f"_eat_plugin_{path.parent.name}_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not build import spec for plugin {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def _register(self, plugin_cls: type[DetectorPlugin], path: Path) -> None:
        """Instantiate the plugin class and index it by name and target."""
        plugin = plugin_cls()
        if not plugin.name:
            raise RuntimeError(f"plugin {plugin_cls!r} (from {path}) has empty .name")
        if plugin.name in self._by_name:
            other = type(self._by_name[plugin.name])
            raise RuntimeError(
                f"duplicate plugin name {plugin.name!r}: {plugin_cls!r} clashes with {other!r}",
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
