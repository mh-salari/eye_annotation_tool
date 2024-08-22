import os
import importlib.util
from .plugin_interface import DetectorPlugin


class PluginManager:
    def __init__(self):
        self.pupil_detectors = {}
        self.iris_detectors = {}
        self.eyelid_detectors = {}
        self.load_plugins()

    def load_plugins(self):
        plugin_dirs = [
            os.path.join(os.path.dirname(__file__), "plugins", "pupil_detectors"),
            os.path.join(os.path.dirname(__file__), "plugins", "iris_detectors"),
            os.path.join(os.path.dirname(__file__), "plugins", "eyelid_detectors"),
        ]

        for plugin_dir in plugin_dirs:
            self.load_plugins_from_directory(plugin_dir)

    def load_plugins_from_directory(self, directory):
        plugin_type = os.path.basename(directory)
        for filename in os.listdir(directory):
            if filename.endswith(".py") and not filename.startswith("__"):
                module_name = filename[:-3]
                module_path = os.path.join(directory, filename)
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for item_name in dir(module):
                    item = getattr(module, item_name)
                    if (
                        isinstance(item, type)
                        and issubclass(item, DetectorPlugin)
                        and item is not DetectorPlugin
                    ):
                        plugin_instance = item()
                        if plugin_type == "pupil_detectors":
                            self.pupil_detectors[plugin_instance.name] = plugin_instance
                        elif plugin_type == "iris_detectors":
                            self.iris_detectors[plugin_instance.name] = plugin_instance
                        elif plugin_type == "eyelid_detectors":
                            self.eyelid_detectors[plugin_instance.name] = (
                                plugin_instance
                            )
                        else:
                            print(f"Unknown plugin type: {plugin_type}")

    def get_pupil_detector(self, name):
        return self.pupil_detectors.get(name)

    def get_iris_detector(self, name):
        return self.iris_detectors.get(name)

    def get_pupil_detector_names(self):
        return list(self.pupil_detectors.keys())

    def get_iris_detector_names(self):
        return list(self.iris_detectors.keys())

    def get_eyelid_detector(self, name):
        return self.eyelid_detectors.get(name)

    def get_eyelid_detector_names(self):
        return list(self.eyelid_detectors.keys())
