from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctp500_app.config import UserSettings, load_user_settings, save_user_settings


class UserSettingsTests(unittest.TestCase):
    def test_column_widths_round_trip_with_address(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            settings = UserSettings(
                bluetooth_address="AA:BB:CC:DD:EE:FF",
                column_widths={"All": {"Name": 321}, "Weapons": {"Element": 140}},
                character_settings={"power_level": 7, "race_weights": {"Human": 2.5}},
                encounter_generation_settings={"party_size": 5, "party_power_level": 8},
                dataset_url="https://docs.google.com/spreadsheets/d/aaaaaaaaaaaaaaaaaaaa/edit?usp=sharing",
                initiative_presets=(("Players", 15), ("End of round", 0)),
            )
            with patch("ctp500_app.config._config_path", return_value=path):
                save_user_settings(settings)
                self.assertEqual(load_user_settings(), settings)

    def test_invalid_saved_widths_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "bluetooth_address": "printer",
                        "column_widths": {
                            "All": {"Name": 250, "Tiny": 10, "Boolean": True},
                            "Broken": "not a width map",
                        },
                        "initiative_presets": [["Players", 12], ["", 5], ["Bad", True], ["Low", -999]],
                    }
                ),
                encoding="utf-8",
            )
            with patch("ctp500_app.config._config_path", return_value=path):
                settings = load_user_settings()

        self.assertEqual(settings.bluetooth_address, "printer")
        self.assertEqual(settings.column_widths, {"All": {"Name": 250}})
        self.assertEqual(settings.initiative_presets, (("Players", 12), ("Low", -100)))


if __name__ == "__main__":
    unittest.main()
