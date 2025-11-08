"""Plugin manager for loading and managing detector plugins."""

import importlib.util
from pathlib import Path

from .plugin_interface import DetectorPlugin


class PluginManager:
    """Manages loading and accessing detector plugins for pupil, iris, and eyelid detection."""

    def __init__(self) -> None:
        """Initialize the PluginManager."""
        self.pupil_detectors = {}
        self.iris_detectors = {}
        self.eyelid_detectors = {}
        self.load_plugins()

    def load_plugins(self) -> None:
        """Load all detector plugins from the plugins directory."""
        plugin_dirs = [
            Path(__file__).parent / "plugins" / "pupil_detectors",
            Path(__file__).parent / "plugins" / "iris_detectors",
            Path(__file__).parent / "plugins" / "eyelid_detectors",
        ]

        for plugin_dir in plugin_dirs:
            self.load_plugins_from_directory(plugin_dir)

    def load_plugins_from_directory(self, directory: Path) -> None:
        """Load plugins from a specific directory and register them by type.

        Args:
            directory: Path to the directory containing plugin files.

        """
        plugin_type = Path(directory).name
        for file_path in Path(directory).iterdir():
            if file_path.suffix == ".py" and not file_path.name.startswith("__"):
                module_name = file_path.stem
                module_path = str(file_path)
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for item_name in dir(module):
                    item = getattr(module, item_name)
                    if isinstance(item, type) and issubclass(item, DetectorPlugin) and item is not DetectorPlugin:
                        plugin_instance = item()
                        if plugin_type == "pupil_detectors":
                            self.pupil_detectors[plugin_instance.name] = plugin_instance
                        elif plugin_type == "iris_detectors":
                            self.iris_detectors[plugin_instance.name] = plugin_instance
                        elif plugin_type == "eyelid_detectors":
                            self.eyelid_detectors[plugin_instance.name] = plugin_instance
                        else:
                            print(f"Unknown plugin type: {plugin_type}")

    def get_pupil_detector(self, name: str) -> DetectorPlugin | None:
        """Get a pupil detector plugin by name.

        Args:
            name: Name of the pupil detector.

        Returns:
            The detector plugin instance or None if not found.

        """
        return self.pupil_detectors.get(name)

    def get_iris_detector(self, name: str) -> DetectorPlugin | None:
        """Get an iris detector plugin by name.

        Args:
            name: Name of the iris detector.

        Returns:
            The detector plugin instance or None if not found.

        """
        return self.iris_detectors.get(name)

    def get_pupil_detector_names(self) -> list[str]:
        """Get list of available pupil detector names.

        Returns:
            List of pupil detector names.

        """
        return list(self.pupil_detectors.keys())

    def get_iris_detector_names(self) -> list[str]:
        """Get list of available iris detector names.

        Returns:
            List of iris detector names.

        """
        return list(self.iris_detectors.keys())

    def get_eyelid_detector(self, name: str) -> DetectorPlugin | None:
        """Get an eyelid detector plugin by name.

        Args:
            name: Name of the eyelid detector.

        Returns:
            The detector plugin instance or None if not found.

        """
        return self.eyelid_detectors.get(name)

    def get_eyelid_detector_names(self) -> list[str]:
        """Get list of available eyelid detector names.

        Returns:
            List of eyelid detector names.

        """
        return list(self.eyelid_detectors.keys())
