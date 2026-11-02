from __future__ import annotations

import tempfile
import unittest
import random
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

from ctp500_app.catalog import parse_category_csv
from ctp500_app.character_store import CharacterStore
from ctp500_app.character_data import RACE_GROUPS
from ctp500_app.characters import (
    Character,
    CharacterGenerator,
    EquipmentSelection,
    GeneratorSettings,
    SKILL_NAMES,
    ToughnessBreakdown,
    canonical_manufacturer,
    parse_modifiers,
    parse_price,
    character_text,
    essential_item_text,
    format_speed,
    speed_distance_feet,
)
from ctp500_app.physical_descriptions import PhysicalDescriptionEngine, threshold_band
from ctp500_app.models import CatalogSnapshot, PrintDocument
from ctp500_app.rendering import render_document


def snapshot(*items) -> CatalogSnapshot:
    return CatalogSnapshot(tuple(items), datetime.now().astimezone(), "test-digest")


class CharacterGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = (
            *parse_category_csv("Weapons", 1, b"Manufacturer,Name,Element,Rank,Range,Form Factor,Clip,Ammo,Special Notes,Price\nArdent Dynamics,Sidearm,Kinetic,4,30,Pistol,8,9mm Cartridge,-,100\n"),
            *parse_category_csv("Consumables/Gear", 2, b"Manufacturer,Name,Type,Effect,Rank,Special Notes,Description,Price\nAll,9mm Cartridge,AMMO,Ammunition,-,-,-,1\nAll,Rope,MILITARY EQUIPMENT,Useful,-,-,-,5\n"),
            *parse_category_csv("Apparel", 3, b"Manufacturer,Name,Coverage,Effects,Rank,Description,Special Notes,Price\nPan-Odyne Defense,Vest,Chest,Protection 3,3,-,-,50\n"),
            *parse_category_csv("Bionics", 4, b"Manufacturer,Name,Type,Effects,Rank,Description,Special Notes,Price,Cap\nAll,Neuralware,Neuralware,Feature 1,1,-,-,20,0.1\nAll,Chipslot Socket,Neuralware,Feature 1,1,-,Requires Neuralware,20,0.1\n"),
            *parse_category_csv("Gene Treatments", 5, b"Manufacturer,Name,Effects,Description,Price,Corruption\nAeon Spire Labs,Conditioning,Enhanced Strength 1,-,10,1D4\n"),
        )
        self.snapshot = snapshot(*self.items)

    def test_seed_is_reproducible_except_identity_and_timestamps(self) -> None:
        settings = GeneratorSettings(budget=500, race="Human")
        one = CharacterGenerator().generate(self.snapshot, settings, seed=123)
        two = CharacterGenerator().generate(self.snapshot, settings, seed=123)
        self.assertEqual(one.base_abilities, two.base_abilities)
        self.assertEqual(one.defense_training, two.defense_training)
        self.assertEqual([(x.name, x.manufacturer) for x in one.equipment], [(x.name, x.manufacturer) for x in two.equipment])

    def test_disabled_races_are_not_eligible(self) -> None:
        settings = GeneratorSettings(race="Human", race_enabled={"Human": False})
        with self.assertRaisesRegex(ValueError, "excluded"):
            CharacterGenerator().generate(self.snapshot, settings, seed=1)

    def test_no_enabled_races_is_rejected(self) -> None:
        settings = GeneratorSettings(race_enabled={race.name: False for race in CharacterGenerator().races})
        with self.assertRaisesRegex(ValueError, "Enable at least one"):
            CharacterGenerator().generate(self.snapshot, settings, seed=1)

    def test_parent_weight_is_rolled_before_subrace_weight(self) -> None:
        parent_weights = {parent: 0 for parent in RACE_GROUPS}
        parent_weights["Gelosean"] = 3
        race_enabled = {race.name: False for race in CharacterGenerator().races}
        race_enabled["Gelosean: Red"] = True
        race_enabled["Gelosean: Blue"] = True
        race_weights = {"Gelosean: Red": 0, "Gelosean: Blue": 5}
        settings = GeneratorSettings(
            parent_weights=parent_weights,
            race_enabled=race_enabled,
            race_weights=race_weights,
        )
        for seed in range(10):
            character = CharacterGenerator().generate(self.snapshot, settings, seed=seed)
            self.assertEqual(character.race, "Gelosean: Blue")

    def test_random_role_is_seeded_and_resolves_to_a_real_role(self) -> None:
        settings = GeneratorSettings(role="Random", race="Human")
        first = CharacterGenerator().generate(self.snapshot, settings, seed=121)
        second = CharacterGenerator().generate(self.snapshot, settings, seed=121)
        self.assertEqual(first.role, second.role)
        self.assertNotEqual(first.role, "Random")

    def test_inventory_manufacturer_rule_is_attribution_only(self) -> None:
        inventory = parse_category_csv(
            "Consumables/Gear", 21,
            b"Manufacturer,Name,Type,Effect,Rank,Special Notes,Description,Price\nOmniMart,Tonic,DRUGS,Enhanced Strength 2,-,-,-,100\n",
        )[0]
        character = CharacterGenerator().generate(snapshot(), GeneratorSettings(race="Human", budget=500), seed=3)
        selected = CharacterGenerator()._selection(random.Random(1), inventory, character, GeneratorSettings(budget=500), (inventory,))
        self.assertIsNotNone(selected)
        self.assertEqual(selected.manufacturer, "OmniMart")
        self.assertEqual(selected.adjusted_price, 100)
        self.assertEqual(selected.notes, [])
        self.assertEqual(selected.modifiers.get("STR"), 2)  # The item's own effect still applies.

    def test_generated_drugs_and_grenades_use_bounded_stack_quantities(self) -> None:
        drug = parse_category_csv(
            "Consumables/Gear", 30,
            b"Manufacturer,Name,Type,Effect,Rank,Special Notes,Description,Price\nAll,Combat Stim,DRUGS,Enhanced Strength 2,-,-,-,10\n",
        )[0]
        grenade = parse_category_csv(
            "Consumables/Gear", 31,
            b"Manufacturer,Name,Type,Effect,Rank,Special Notes,Description,Price\nAll,Shock Grenade,GRENADES,Affliction 5,5,-,-,8\n",
        )[0]
        settings = GeneratorSettings(
            race="Human", budget=500,
            category_initial={section: int(section == "Inventory") for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
            category_continue={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
            category_max={section: int(section == "Inventory") for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
        )

        drug_quantities = set()
        grenade_quantities = set()
        for seed in range(30):
            generated_drug = CharacterGenerator().generate(snapshot(drug), settings, seed=seed)
            generated_grenade = CharacterGenerator().generate(snapshot(grenade), settings, seed=seed)
            drug_item = generated_drug.equipment[0]
            grenade_item = generated_grenade.equipment[0]
            drug_quantities.add(drug_item.quantity)
            grenade_quantities.add(grenade_item.quantity)
            self.assertEqual(drug_item.adjusted_price, 10 * drug_item.quantity)
            self.assertEqual(grenade_item.adjusted_price, 8 * grenade_item.quantity)

        self.assertEqual(drug_quantities, {1, 2, 3})
        self.assertEqual(grenade_quantities, {1, 2})

    def test_disabled_parent_blocks_manual_subrace(self) -> None:
        settings = GeneratorSettings(race="Deep Spacer: Vanor", parent_enabled={"Deep Spacer": False})
        with self.assertRaisesRegex(ValueError, "excluded"):
            CharacterGenerator().generate(self.snapshot, settings, seed=2)

    def test_known_modifiers_are_conservative(self) -> None:
        self.assertEqual(parse_modifiers("Enhanced Agility 2, Protection 5", armor=True), {"AGL": 2, "TOUGH": 5})
        self.assertEqual(parse_modifiers("+3 Fortitude resistance against poison"), {"FORT": 3})

    def test_disabled_quality_skips_price_weighting_logic(self) -> None:
        settings = GeneratorSettings(
            race="Human", budget=500, quality=1, quality_enabled=False,
            category_initial={"Weapons": 1, "Armor": 0, "Bionics": 0, "Genemods": 0, "Inventory": 0},
            category_continue={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
            category_max={"Weapons": 1, "Armor": 0, "Bionics": 0, "Genemods": 0, "Inventory": 0},
        )
        with patch("ctp500_app.characters.math.exp", side_effect=AssertionError("quality logic ran")):
            CharacterGenerator().generate(self.snapshot, settings, seed=3)

    def test_disabled_quantity_matches_zero_quantity_generation(self) -> None:
        disabled = GeneratorSettings(race="Human", budget=500, quantity=1, quantity_enabled=False)
        neutral = GeneratorSettings(race="Human", budget=500, quantity=0, quantity_enabled=True)
        one = CharacterGenerator().generate(self.snapshot, disabled, seed=33)
        two = CharacterGenerator().generate(self.snapshot, neutral, seed=33)
        self.assertEqual(
            [(item.category, item.name, item.manufacturer) for item in one.equipment],
            [(item.category, item.name, item.manufacturer) for item in two.equipment],
        )

    def test_aliases_are_normalized(self) -> None:
        self.assertEqual(canonical_manufacturer("Hexatech Neural"), "Hexatek Neural")
        self.assertEqual(canonical_manufacturer("Granreale Foods"), "Granrealé Foods")
        self.assertEqual(parse_price("1/100rnds"), 1)
        self.assertEqual(parse_price("400 @ 2/rnd"), 400)

    def test_multi_manufacturer_item_resolves_one_persistent_choice(self) -> None:
        item = parse_category_csv(
            "Weapons", 9,
            b'Manufacturer,Name,Element,Rank,Range,Form Factor,Clip,Ammo,Special Notes,Price\n"Ardent Dynamics, Caliber Collective",Split Brand,Kinetic,4,30,Pistol,8,-,-,100\n',
        )[0]
        character = CharacterGenerator().generate(snapshot(*self.items), GeneratorSettings(race="Human", budget=500), seed=9)
        maker = CharacterGenerator()._choose_maker(random.Random(4), item, character, GeneratorSettings(affinity_strength=0))
        self.assertIn(maker, {"Ardent Dynamics", "Caliber Collective"})
        selected = CharacterGenerator()._selection(random.Random(4), item, character, GeneratorSettings(affinity_strength=0, budget=500))
        self.assertIsNotNone(selected)
        self.assertIn(selected.manufacturer, {"Ardent Dynamics", "Caliber Collective"})
        restored = EquipmentSelection(**selected.__dict__) if hasattr(selected, "__dict__") else EquipmentSelection(**{field: getattr(selected, field) for field in selected.__dataclass_fields__})
        self.assertEqual(restored.manufacturer, selected.manufacturer)

    def test_all_expands_to_every_manufacturer_in_its_category(self) -> None:
        all_weapon = parse_category_csv(
            "Weapons", 10,
            b"Manufacturer,Name,Element,Rank,Range,Form Factor,Clip,Ammo,Special Notes,Price\nAll,Universal Sidearm,Kinetic,4,30,Pistol,8,-,-,100\n",
        )[0]
        other_weapon = parse_category_csv(
            "Weapons", 11,
            b"Manufacturer,Name,Element,Rank,Range,Form Factor,Clip,Ammo,Special Notes,Price\nCaliber Collective,Budget Sidearm,Kinetic,4,30,Pistol,8,-,-,100\n",
        )[0]
        unrelated_armor = parse_category_csv(
            "Apparel", 12,
            b"Manufacturer,Name,Coverage,Effects,Rank,Description,Special Notes,Price\nSilkline Apparel,Coat,Chest,Protection 1,1,-,-,100\n",
        )[0]
        catalog = (all_weapon, other_weapon, unrelated_armor, self.items[0])
        character = CharacterGenerator().generate(snapshot(*self.items), GeneratorSettings(race="Human", budget=500), seed=9)
        possible = CharacterGenerator()._possible_makers(all_weapon, catalog)
        self.assertEqual(possible, ["Ardent Dynamics", "Caliber Collective"])
        selected = CharacterGenerator()._selection(random.Random(2), all_weapon, character, GeneratorSettings(affinity_strength=0, budget=500), catalog)
        self.assertIsNotNone(selected)
        self.assertIn(selected.manufacturer, possible)
        self.assertNotEqual(selected.manufacturer, "All")

    def test_corruption_penalty_ledger_is_stable(self) -> None:
        settings = GeneratorSettings(race="Human", budget=500)
        character = CharacterGenerator().generate(self.snapshot, settings, seed=444)
        character.equipment.append(EquipmentSelection("gene", "Genemods", 5, 2, "Risky", "All", {}, 0, 0, corruption=25))
        character.recalculate_corruption_penalties()
        first = list(character.corruption_penalties)
        character.recalculate_corruption_penalties()
        self.assertEqual(first, character.corruption_penalties)
        self.assertEqual(len(first), 2)

    def test_weapon_reserves_and_adds_ammunition(self) -> None:
        settings = GeneratorSettings(
            race="Human", budget=101, weapon_mode="Ranged",
            category_initial={"Weapons": 1, "Armor": 0, "Bionics": 0, "Genemods": 0, "Inventory": 0},
            category_continue={"Weapons": 0, "Armor": 0, "Bionics": 0, "Genemods": 0, "Inventory": 0},
            category_max={"Weapons": 1, "Armor": 0, "Bionics": 0, "Genemods": 0, "Inventory": 0},
        )
        character = CharacterGenerator().generate(self.snapshot, settings, seed=7)
        self.assertEqual([item.name for item in character.equipment if item.category == "Weapons"], ["Sidearm"])
        self.assertIn("9mm Cartridge", [item.name for item in character.equipment if item.category == "Inventory"])
        ammo = next(item for item in character.equipment if item.name == "9mm Cartridge")
        self.assertEqual(ammo.quantity, 8)
        self.assertLessEqual(character.spent, character.budget)

    def test_guaranteed_weapon_prefers_an_affordable_weapon(self) -> None:
        settings = GeneratorSettings(
            race="Human", budget=100, weapon_mode="None", guarantee_weapon=True,
            category_initial={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
            category_max={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
        )
        character = CharacterGenerator().generate(self.snapshot, settings, seed=7)
        self.assertEqual([item.name for item in character.equipment if item.category == "Weapons"], ["Sidearm"])
        self.assertLessEqual(character.spent, character.budget)

    def test_guaranteed_weapon_limits_budget_overflow_to_one_eighth(self) -> None:
        weapon = parse_category_csv(
            "Weapons", 30,
            b"Manufacturer,Name,Element,Rank,Range,Form Factor,Clip,Ammo,Special Notes,Price\nAll,Knife,Kinetic,1,-,Melee,-,-,-,10\n",
        )[0]
        inventory = parse_category_csv(
            "Consumables/Gear", 31,
            b"Manufacturer,Name,Type,Effect,Rank,Special Notes,Description,Price\nAll,Kit,MILITARY EQUIPMENT,-,-,-,-,100\n",
        )[0]
        settings = GeneratorSettings(
            race="Human", budget=100, weapon_mode="None", guarantee_weapon=True,
            category_initial={"Weapons": 0, "Armor": 0, "Bionics": 0, "Genemods": 0, "Inventory": 1},
            category_continue={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
            category_max={"Weapons": 0, "Armor": 0, "Bionics": 0, "Genemods": 0, "Inventory": 1},
        )
        character = CharacterGenerator().generate(snapshot(weapon, inventory), settings, seed=7)
        guaranteed_weapon = next(item for item in character.equipment if item.category == "Weapons")
        self.assertEqual(guaranteed_weapon.name, "Knife")
        self.assertLessEqual(guaranteed_weapon.adjusted_price, character.budget / 8)

    def test_guaranteed_weapon_rejects_catalog_without_weapons(self) -> None:
        settings = GeneratorSettings(race="Human", guarantee_weapon=True)
        with self.assertRaisesRegex(ValueError, "no eligible weapons"):
            CharacterGenerator().generate(snapshot(), settings, seed=7)

    def test_bionic_prerequisite_must_already_be_installed(self) -> None:
        generator = CharacterGenerator()
        neuralware = next(item for item in self.items if item.name == "Neuralware")
        chipslot = next(item for item in self.items if item.name == "Chipslot Socket")
        character = generator.generate(snapshot(), GeneratorSettings(race="Human"), seed=3)
        self.assertFalse(generator._prerequisites_met(chipslot, character))
        character.equipment.append(EquipmentSelection("neural", "Bionics", neuralware.source_gid, neuralware.source_row, neuralware.name, "All", dict(neuralware.fields), 20, 20, .1))
        self.assertTrue(generator._prerequisites_met(chipslot, character))

    def test_required_neuralware_is_forced_into_generated_bionics(self) -> None:
        settings = GeneratorSettings(
            race="Human", budget=500,
            category_initial={"Weapons": 0, "Armor": 0, "Bionics": 1, "Genemods": 0, "Inventory": 0},
            category_continue={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
            category_max={"Weapons": 0, "Armor": 0, "Bionics": 1, "Genemods": 0, "Inventory": 0},
        )
        # With only the dependent item eligible as the rolled item, its base
        # Neuralware must be installed in addition to the section maximum.
        dependent = next(item for item in self.items if item.name == "Chipslot Socket")
        neuralware = next(item for item in self.items if item.name == "Neuralware")
        character = CharacterGenerator().generate(snapshot(dependent, neuralware), settings, seed=0)
        self.assertTrue({"Chipslot Socket", "Neuralware"} <= {item.name for item in character.equipment})

    def test_skill_chip_resolves_skill_and_forces_chipslot_and_neuralware(self) -> None:
        bionics = parse_category_csv(
            "Bionics", 40,
            b"Manufacturer,Name,Type,Effects,Rank,Description,Special Notes,Price,Cap\n"
            b"All,Neuralware,Neuralware,Feature 1,1,-,-,20,0\n"
            b"All,Chipslot Socket,Neuralware,Feature 1,1,-,Requires Neuralware,20,0\n"
            b"All,Skill Chip Mk2,Chipware,Enhanced Skill (+2 to one preloaded skill),2,-,Requires Neuralware and a chipslot socket,30,0\n",
        )

        class SkillFirstGenerator(CharacterGenerator):
            def _eligible(self, character, items, category):
                eligible = super()._eligible(character, items, category)
                skill_chips = [item for item in eligible if self._is_skill_chip(item)]
                return skill_chips or eligible

        settings = GeneratorSettings(
            race="Human", budget=500,
            category_initial={"Weapons": 0, "Armor": 0, "Bionics": 1, "Genemods": 0, "Inventory": 0},
            category_continue={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
            category_max={"Weapons": 0, "Armor": 0, "Bionics": 1, "Genemods": 0, "Inventory": 0},
        )
        character = SkillFirstGenerator().generate(snapshot(*bionics), settings, seed=12)
        names = {item.name for item in character.equipment}
        chip = next(item for item in character.equipment if item.fields.get("Selected Skill"))
        skill = chip.fields["Selected Skill"]

        self.assertIn("Neuralware", names)
        self.assertIn("Chipslot Socket", names)
        self.assertIn(skill, SKILL_NAMES)
        self.assertEqual(chip.name, f"Skill Chip Mk2 ({skill})")
        self.assertEqual(chip.fields["Effects"], f"Enhanced Skill (+2 to {skill})")
        self.assertEqual(character.skill_chip_bonuses(), {skill: 2})
        self.assertIn(f"Skill chips: {skill}:+2", character_text(character))

    def test_generation_keeps_bionics_that_exceed_capacity(self) -> None:
        oversized = parse_category_csv(
            "Bionics", 41,
            b"Manufacturer,Name,Type,Effects,Rank,Description,Special Notes,Price,Cap\n"
            b"All,Impossible Implant,Implant,Feature 1,1,-,-,10,100\n",
        )[0]
        settings = GeneratorSettings(
            race="Human", budget=500,
            category_initial={"Weapons": 0, "Armor": 0, "Bionics": 1, "Genemods": 0, "Inventory": 0},
            category_continue={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
            category_max={"Weapons": 0, "Armor": 0, "Bionics": 1, "Genemods": 0, "Inventory": 0},
        )
        character = CharacterGenerator().generate(snapshot(oversized), settings, seed=2)
        self.assertIn("Impossible Implant", {item.name for item in character.equipment})
        used, capacity = character.bionic_capacity()
        self.assertGreater(used, capacity)

    def test_generation_adds_seeded_lootable_credit_chip_from_remaining_budget(self) -> None:
        settings = GeneratorSettings(
            race="Human", budget=1_000,
            category_initial={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
        )
        one = CharacterGenerator().generate(snapshot(), settings, seed=81)
        two = CharacterGenerator().generate(snapshot(), settings, seed=81)
        chip = next(item for item in one.equipment if item.fields.get("Type") == "Currency")
        other_chip = next(item for item in two.equipment if item.fields.get("Type") == "Currency")
        percentage = int(chip.fields["Special Notes"].split("rolled ", 1)[1].split("%", 1)[0])
        amount = float(chip.fields["Amount"].split()[0])
        self.assertGreaterEqual(percentage, 0)
        self.assertLessEqual(percentage, 100)
        self.assertEqual(amount, round((one.budget - one.spent) * percentage / 100))
        self.assertEqual(amount, int(amount))
        self.assertEqual(chip.fields, other_chip.fields)
        self.assertIn(chip.fields["Amount"], chip.name)
        self.assertEqual(chip.adjusted_price, 0)


class CharacterPersistenceTests(unittest.TestCase):
    @staticmethod
    def _speed_character(race: str = "Human", equipment=None) -> Character:
        now = datetime.now().astimezone().isoformat()
        return Character(
            "speed", "Runner", race, "Generalist", 5, 1, "digest", 100, 0,
            {key: 0 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
            {key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
            list(equipment or []), [], [], {}, now, now,
        )

    def test_speed_has_rank_two_baseline_and_uses_mnm_distance_table(self) -> None:
        self.assertEqual(self._speed_character().totals()[2], 2)
        expected = {2: 30, 3: 60, 4: 120, 5: 250, 6: 500, 7: 900, 8: 1800, 9: 2640, 10: 5280}
        self.assertEqual({rank: speed_distance_feet(rank) for rank in expected}, expected)
        self.assertEqual(format_speed(2), "2 (30')")
        self.assertEqual(format_speed(8), "8 (1,800')")

    def test_toughness_breakdown_formats_only_applicable_protection_layers(self) -> None:
        self.assertEqual(ToughnessBreakdown(5).text(), "Tough:5")
        self.assertEqual(ToughnessBreakdown(5, armor_bonus=3).text(), "Tough:5 +A:8")
        self.assertEqual(ToughnessBreakdown(5, shield_bonus=2).text(), "Tough:5 +S:7")
        self.assertEqual(
            ToughnessBreakdown(5, armor_bonus=3, shield_bonus=2).text(),
            "Tough:5 +A:8 +S:7 +A+S:10",
        )

    def test_equipment_toughness_defaults_to_armor_and_infers_shields(self) -> None:
        gear = [
            EquipmentSelection("armor", "Bionics", 1, 1, "Subdermal Plating", "Acme", {}, 0, 0, modifiers={"TOUGH": 3}),
            EquipmentSelection("shield", "Inventory", 1, 2, "Barrier", "Acme", {"Effect": "Projected shielding"}, 0, 0, modifiers={"TOUGH": 2}),
            EquipmentSelection("penalty", "Armor", 1, 3, "Damaged Suit", "Acme", {}, 0, 0, modifiers={"TOUGH": -1}),
        ]
        character = self._speed_character(equipment=gear)
        character.base_abilities["STA"] = 4
        character.defense_training["TOUGH"] = 1

        self.assertEqual(character.protection_bonuses(), (3, 2))
        self.assertEqual(character.toughness_breakdown().text(), "Tough:4 +A:7 +S:6 +A+S:9")
        self.assertIn("Tough:4 +A:7 +S:6 +A+S:9", character_text(character))

    def test_speed_power_ranks_stack_from_race_and_equipment_text(self) -> None:
        gear = [
            EquipmentSelection("one", "Bionics", 1, 1, "Legs", "All", {"Effects": "Speed 1"}, 0, 0),
            EquipmentSelection("two", "Genemods", 1, 2, "Reflexes", "All", {"Effect": "Speed 2"}, 0, 0),
        ]
        self.assertEqual(self._speed_character(equipment=gear).totals()[2], 5)
        self.assertEqual(self._speed_character("Kangaa").totals()[2], 4)
        self.assertEqual(self._speed_character("Deep Spacer: Renpatar").totals()[2], 4)

    def test_physical_description_uses_thresholds_and_hides_transformation_flags(self) -> None:
        engine = PhysicalDescriptionEngine()
        text = engine.describe(
            race="Human",
            stats={"STR": 10, "STA": 6, "AGL": 3, "FGT": 3, "AWE": 6, "DEX": 6, "INT": 3, "PRE": 10},
            seed=42,
            settings={"_transformation_flags": {"crystalline": True, "undead": False}},
        )
        self.assertEqual(threshold_band("STR", 10).label, "massive")
        self.assertEqual(threshold_band("STR", -99).label, "frail")
        self.assertEqual(threshold_band("STR", 999).label, "colossal")
        self.assertIn("massive, clearly superhuman musculature", text)
        self.assertIn("Faceted crystalline growths", text)
        self.assertNotIn("transformation_flags", text)
        self.assertNotIn("crystalline: True", text)
        self.assertNotIn("no ordinary warmth", text)
        self.assertEqual(text, engine.describe(
            race="Human",
            stats={"STR": 10, "STA": 6, "AGL": 3, "FGT": 3, "AWE": 6, "DEX": 6, "INT": 3, "PRE": 10},
            seed=42,
            settings={"_transformation_flags": {"crystalline": True, "undead": False}},
        ))

    def test_character_receipt_contains_top_to_bottom_physical_profile(self) -> None:
        now = datetime.now().astimezone().isoformat()
        character = Character(
            "id", "Profile", "Human", "Generalist", 5, 7, "d", 0, 0,
            {key: 3 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
            {key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
            [], [], [], {}, now, now,
        )
        text = character_text(character, physical_profile_expanded=True)
        positions = [text.index(label) for label in ("Head and face:", "Eyes and senses:", "Neck and torso:", "Arms and hands:", "Hips, legs, and feet:", "Movement:")]
        self.assertEqual(positions, sorted(positions))
        self.assertGreater(text.index("Physical Profile:"), text.index("Corruption:"))
        self.assertNotIn("Physical Profile:", character_text(character))

    def test_physical_profile_uses_base_stats_not_equipment_modified_totals(self) -> None:
        now = datetime.now().astimezone().isoformat()
        implant = EquipmentSelection(
            "implant", "Bionics", 1, 1, "Strength Amplifier", "All", {},
            0, 0, modifiers={"STR": 15},
        )
        character = Character(
            "id", "Profile", "Human", "Generalist", 5, 7, "d", 0, 0,
            {key: 0 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
            {key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
            [implant], [], [], {}, now, now,
        )
        text = character_text(character, physical_profile_expanded=True)
        self.assertIn("an unexceptional amount of muscle", text)
        self.assertNotIn("colossal frame built for impossible force", text)

    def test_snapshot_round_trip_and_receipt(self) -> None:
        now = datetime.now().astimezone().isoformat()
        character = Character(
            "id", "Example", "Human", "Generalist", 5, 1, "digest", 100, 10,
            {key: 1 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
            {key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
            [EquipmentSelection("item", "Weapons", 1, 2, "Sidearm", "Ardent Dynamics", {}, 10, 10)],
            ["Ardent Dynamics"], [], {}, now, now,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = CharacterStore(Path(directory) / "characters.db")
            store.save(character)
            loaded = store.get(character.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.to_dict(), character.to_dict())
            self.assertEqual(len(store.list("Example")), 1)
            self.assertTrue(store.delete(character.id))
        image = render_document(PrintDocument("Example", "Character", "Name: Example\nSTR:1"))
        self.assertEqual(image.width, 384)
        self.assertGreater(image.height, 40)

    def test_expanded_preview_shows_item_data_and_inventory_hides_maker_notes(self) -> None:
        now = datetime.now().astimezone().isoformat()
        item = EquipmentSelection("inv", "Inventory", 1, 1, "Tonic", "OmniMart", {"Effect": "Restore 1", "Price": "5"}, 5, 5, notes=["Manufacturer malfunction effect"])
        character = Character("id", "", "Human", "Generalist", 5, 1, "d", 10, 5,
            {key: 0 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
            {key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")}, [item], [], [], {}, now, now)
        collapsed = character_text(character)
        expanded = character_text(character, expanded_item_ids={"inv"})
        self.assertIn("Tonic [OmniMart]", collapsed)
        self.assertNotIn("Manufacturer malfunction effect", collapsed)
        self.assertNotIn("Effect: Restore 1", collapsed)
        self.assertIn("Effect: Restore 1", expanded)

    def test_weapon_to_hit_uses_attribute_item_and_coordinated_smartgun_bonuses(self) -> None:
        now = datetime.now().astimezone().isoformat()
        weapon = EquipmentSelection(
            "gun", "Weapons", 1, 1, "Smart Pistol", "Brightcorp",
            {"Form Factor": "Pistol", "Special Notes": "Smartgun Link"},
            10, 10, notes=["Attack +1"],
        )
        processor = EquipmentSelection(
            "processor", "Bionics", 2, 1, "Neural Processor", "Vyral Systems",
            {"Type": "Neuralware", "Special Notes": "Prerequisite for any bionic listed as Requires Neuralware"}, 1, 1,
        )
        link = EquipmentSelection(
            "link", "Bionics", 2, 2, "Smartgun Link", "Vyral Systems",
            {"Type": "Neuralware", "Effects": "+2 to attack checks with linked smart weapons"}, 1, 1,
        )
        character = Character(
            "id", "", "Human", "Generalist", 5, 1, "d", 100, 12,
            {"STR": 0, "AGL": 0, "FGT": 3, "AWE": 0, "STA": 1, "DEX": 4, "INT": 0, "PRE": 0},
            {key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
            [weapon, processor, link], [], [], {}, now, now,
        )
        self.assertEqual(character.to_hit_modifier(weapon), 7)
        self.assertIn("- +7 Smart Pistol [Brightcorp]", character_text(character))

    def test_cyberpsychosis_rounds_up_excess_and_penalizes_every_defense(self) -> None:
        now = datetime.now().astimezone().isoformat()
        implant = EquipmentSelection(
            "implant", "Bionics", 1, 1, "Oversized Implant", "All", {},
            0, 0, capacity=4.2,
        )
        character = Character(
            "id", "", "Human", "Generalist", 5, 1, "d", 100, 0,
            {"STR": 0, "AGL": 0, "FGT": 0, "AWE": 0, "STA": 1, "DEX": 0, "INT": 0, "PRE": 0},
            {key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
            [implant], [], [], {}, now, now,
        )
        _abilities, defenses, _speed = character.totals()
        self.assertEqual(character.cyberpsychosis_points(), 2)
        self.assertEqual(defenses, {"DODGE": -10, "PARRY": -10, "FORT": -9, "TOUGH": -9, "WILL": -10})
        self.assertIn('Race: "Human"  WARNING: CyberPsychosis 2 (-10 all defenses)', character_text(character))

    def test_essential_item_output_omits_nonessential_fields(self) -> None:
        item = EquipmentSelection(
            "gear", "Bionics", 1, 1, "Eye", "Vyral Systems",
            {"Effects": "Senses 2", "Rank": "2", "Description": "A detailed description", "Price": "500", "Cap": "0.5", "Effective Rank": "4"},
            500, 750, capacity=.5, notes=["Manufacturer effect"],
        )
        text = essential_item_text(item)
        self.assertIn("Effects: Senses 2", text)
        self.assertIn("Manufacturer: Vyral Systems", text)
        self.assertNotIn("Description", text)
        self.assertNotIn("Price", text)
        self.assertNotIn("Cap", text)
        self.assertNotIn("Effective Rank", text)
        self.assertNotIn("Manufacturer effect", text)

    def test_full_racial_trait_text_preserves_immunity_descriptors(self) -> None:
        nightwalker = next(race for races in RACE_GROUPS.values() for race in races if race.name == "Nightwalker")
        self.assertIn("Disease, Sleep, Suffocation, Starvation Limited to Blood Diet", nightwalker.traits[0])
        self.assertIn("Darkvision, Acute Smell, Tracking, Ultra-Hearing", nightwalker.traits[1])

    def test_gamma_tauist_races_receive_free_cap_exempt_extra_bionics(self) -> None:
        expected = {
            "Gamma Tauist: Tame": 3,
            "Gamma Tauist: Extreme": 6,
            "Gamma Tauist: Forfeiter": 10,
        }
        rows = ["Manufacturer,Name,Type,Effects,Rank,Description,Special Notes,Price,Cap"]
        rows.extend(f"All,Implant {index},Body,+1 Strength,1,-,-,100,1" for index in range(12))
        catalog = snapshot(*parse_category_csv("Bionics", 90, ("\n".join(rows) + "\n").encode()))
        for race, count in expected.items():
            settings = GeneratorSettings(
                race=race, budget=0,
                category_initial={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
                category_continue={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
                category_max={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
            )
            subject = CharacterGenerator().generate(catalog, settings, seed=4)
            bionics = [item for item in subject.equipment if item.category == "Bionics"]
            self.assertEqual(len(bionics), count, race)
            self.assertEqual(subject.spent, 0, race)
            self.assertEqual(subject.bionic_capacity()[0], 0, race)
            self.assertTrue(all(item.adjusted_price == 0 and item.capacity == 0 for item in bionics), race)

        expected_with_one_normal = {
            "Gamma Tauist: Tame": 4,
            "Gamma Tauist: Extreme": 8,
            "Gamma Tauist: Forfeiter": 12,
        }
        for race, total in expected_with_one_normal.items():
            settings = GeneratorSettings(
                race=race, budget=100,
                category_initial={section: int(section == "Bionics") for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
                category_continue={section: 0 for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
                category_max={section: int(section == "Bionics") for section in ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")},
            )
            subject = CharacterGenerator().generate(catalog, settings, seed=4)
            bionics = [item for item in subject.equipment if item.category == "Bionics"]
            racial = [item for item in bionics if item.adjusted_price == 0 and item.capacity == 0]
            self.assertEqual(len(bionics), total, race)
            self.assertEqual(len(racial), total - 1, race)
            self.assertEqual(subject.spent, 100, race)
            self.assertEqual(subject.bionic_capacity()[0], 1, race)


if __name__ == "__main__":
    unittest.main()
