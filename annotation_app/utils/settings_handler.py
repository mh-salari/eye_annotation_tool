"""Handler for application settings persistence."""

import json
from pathlib import Path

DEFAULT_SETTINGS = {
    "pupil_detector": "Pupil Core",
    "iris_detector": "disabled",
    "eyelid_detector": "disabled",
}


class SettingsHandler:
    """Manages loading, saving, and accessing application settings."""

    def __init__(self) -> None:
        """Initialize the SettingsHandler."""
        self.settings_file = str(Path(__file__).parent / ".." / ".." / "ai" / "settings.json")
        self.settings = self.load_settings()

    def load_settings(self) -> dict:
        """Load settings from file or return defaults.

        Returns:
            Dictionary containing settings.

        """
        if Path(self.settings_file).exists():
            with Path(self.settings_file).open(encoding="utf-8") as f:
                return json.load(f)
        return DEFAULT_SETTINGS.copy()

    def save_settings(self) -> None:
        """Save current settings to file."""
        with Path(self.settings_file).open("w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=4)

    def get_setting(self, key: str) -> str:
        """Get a setting value by key.

        Args:
            key: Setting key to retrieve.

        Returns:
            The setting value.

        """
        return self.settings.get(key, DEFAULT_SETTINGS.get(key))

    def set_setting(self, key: str, value: str) -> None:
        """Set a setting value and save to file.

        Args:
            key: Setting key to update.
            value: New setting value.

        """
        self.settings[key] = value
        self.save_settings()
