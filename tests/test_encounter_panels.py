"""Headless regression tests for the four-pane encounter content model."""
from __future__ import annotations

import inspect
import unittest

from ctp500_app.characters import Character, EquipmentSelection
from ctp500_app.encounter_panels import encounter_panel_content, feature_label
from ctp500_app.encounters import ActionCard, Combatant
from ctp500_app.ui import GearPrinterApp


def equipment(identity: str, category: str, name: str, *, effects: str = "", quantity: int = 1):
    return EquipmentSelection(
        id=identity, category=category, source_gid=1, source_row=1, name=name,
        manufacturer="Test", fields={"Effects": effects}, base_price=0,
        adjusted_price=0, quantity=quantity,
    )


def equipped_combatant():
    character = Character(
        id="char", name="Corvid Tester", race="Corvid", role="Generalist",
        power_level=8, seed=1, catalog_digest="", budget=10000, spent=0,
        base_abilities={key: 0 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
        defense_training={key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
        equipment=[
            equipment("rifle", "Weapons", "Rifle"),
            equipment("arm", "Bionics", "Bionic Arm", effects="Damage 4"),
            equipment("ammo", "Inventory", "9mm Cartridge", quantity=8),
            equipment("armor", "Armor", "Bulletproof Vest"),
            equipment("genemod", "Genemods", "Agility Mod"),
        ],
        affinities=[], corruption_penalties=[], settings={},
        created_at="", updated_at="",
    )
    actor = Combatant.from_character(character)
    actor.actions.append(ActionCard(id="custom-power", name="Psi Burst", source="Manual"))
    return actor


class EncounterPanelTests(unittest.TestCase):
    def test_none_and_ad_hoc_characters_still_have_valid_sections(self):
        self.assertEqual(encounter_panel_content(None).racial_traits, ())
        self.assertEqual(encounter_panel_content(Combatant.ad_hoc("Marker", 5)).bionics, ())

    def test_bionics_and_other_equipment_do_not_overlap(self):
        result = encounter_panel_content(equipped_combatant())
        self.assertEqual([item.name for item in result.bionics], ["Bionic Arm"])
        self.assertEqual({item.name for item in result.inventory}, {
            "9mm Cartridge", "Bulletproof Vest", "Agility Mod",
        })
        self.assertFalse(any(item.category == "Weapons" for item in result.inventory))

    def test_racial_traits_and_special_actions_are_displayed(self):
        result = encounter_panel_content(equipped_combatant())
        self.assertGreaterEqual(len(result.racial_traits), 1)
        self.assertEqual([action.name for action in result.special_actions], ["Psi Burst"])
        self.assertIn("Bionic Arm", [action.name for action in result.actions])
        self.assertIn("Psi Burst", [action.name for action in result.actions])

    def test_compact_racial_trait_labels_preserve_full_description(self):
        self.assertEqual(feature_label("Winged Form (Flight 4; Extended Vision)"),
                         ("Winged Form", "(Flight 4; Extended Vision)"))
        self.assertEqual(feature_label("Keen Senses"), ("Keen Senses", ""))

    def test_quadrant_layout_uses_independent_resizable_panes(self):
        source = inspect.getsource(GearPrinterApp._build_encounter_ui)
        self.assertIn('sections = ttk.Panedwindow(right, orient="vertical")', source)
        self.assertIn('upper = ttk.Panedwindow(sections, orient="horizontal")', source)
        self.assertIn('lower_row = ttk.Panedwindow(sections, orient="horizontal")', source)
        self.assertIn('text="Weapons / Actions"', source)
        self.assertIn('text="Bionics"', source)
        self.assertIn('text="Inventory"', source)
        self.assertIn('text="Racial Abilities / Special Powers"', source)
        self.assertIn('resolver.pack(fill="x", side="bottom")', source)
        builder = inspect.getsource(GearPrinterApp._build_encounter_section_tree)
        self.assertIn('ttk.Scrollbar(body, orient="vertical"', builder)
        self.assertIn('ttk.Scrollbar(parent, orient="horizontal"', builder)
        sash = inspect.getsource(GearPrinterApp._set_encounter_sections_sashes)
        self.assertIn('self.encounter_sections_upper.sashpos', sash)
        self.assertIn('self.encounter_sections_lower.sashpos', sash)


if __name__ == "__main__":
    unittest.main()
