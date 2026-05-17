"""Auto-detector plugin system for eye annotations."""

from .plugin_interface import DetectorPlugin
from .plugin_manager import PluginManager

__all__ = ["DetectorPlugin", "PluginManager"]
