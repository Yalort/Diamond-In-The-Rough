"""Regression tests for the encounter manager's persistent navigation control."""
from __future__ import annotations

import inspect
import unittest

from ctp500_app.ui import GearPrinterApp


class StubPane:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def pack(self, **kwargs):
        self.calls.append(("pack", kwargs))

    def pack_forget(self):
        self.calls.append(("pack_forget", {}))


class StubButton:
    def __init__(self):
        self.label = ""

    def configure(self, **kwargs):
        self.label = kwargs["text"]


class EncounterManagerToggleTests(unittest.TestCase):
    def test_manager_button_is_in_persistent_header(self):
        source = inspect.getsource(GearPrinterApp._build_encounter_ui)
        header = source.split("panes = ttk.Panedwindow", 1)[0]
        self.assertIn('text="Hide Manager"', header)
        self.assertIn("command=self._toggle_encounter_library", header)

    def test_manager_can_reopen_after_auto_collapse(self):
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.encounter_library_expanded = True
        app.encounter_library_body = StubPane()
        app.encounter_roster_controls = StubPane()
        app.encounter_manager_button = StubButton()

        app._toggle_encounter_library(False)
        self.assertFalse(app.encounter_library_expanded)
        self.assertEqual(app.encounter_manager_button.label, "Show Manager")
        self.assertEqual(app.encounter_library_body.calls[-1][0], "pack_forget")

        app._toggle_encounter_library()
        self.assertTrue(app.encounter_library_expanded)
        self.assertEqual(app.encounter_manager_button.label, "Hide Manager")
        method, kwargs = app.encounter_library_body.calls[-1]
        self.assertEqual(method, "pack")
        self.assertIs(kwargs["before"], app.encounter_roster_controls)

    def test_repeated_force_updates_button_even_when_state_is_unchanged(self):
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.encounter_library_expanded = False
        app.encounter_library_body = StubPane()
        app.encounter_roster_controls = StubPane()
        app.encounter_manager_button = StubButton()

        app._toggle_encounter_library(False)
        self.assertFalse(app.encounter_library_body.calls)
        self.assertEqual(app.encounter_manager_button.label, "Show Manager")


if __name__ == "__main__":
    unittest.main()
