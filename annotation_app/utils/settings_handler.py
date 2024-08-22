import json
import os

DEFAULT_SETTINGS = {
    "pupil_detector": "Pupil Core",
    "iris_detector": "disabled",
    "eyelid_detector": "disabled",
}


class SettingsHandler:
    def __init__(self):
        self.settings_file = os.path.join(
            os.path.dirname(__file__), "..", "..", "ai", "settings.json"
        )
        self.settings = self.load_settings()

    def load_settings(self):
        if os.path.exists(self.settings_file):
            with open(self.settings_file, "r") as f:
                return json.load(f)
        return DEFAULT_SETTINGS.copy()

    def save_settings(self):
        with open(self.settings_file, "w") as f:
            json.dump(self.settings, f, indent=4)

    def get_setting(self, key):
        return self.settings.get(key, DEFAULT_SETTINGS.get(key))

    def set_setting(self, key, value):
        self.settings[key] = value
        self.save_settings()
