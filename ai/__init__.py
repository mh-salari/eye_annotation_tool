"""AI plugin system for eye annotation detectors."""

from .plugin_interface import DetectorPlugin
from .plugin_manager import PluginManager

__all__ = ["DetectorPlugin", "PluginManager"]
