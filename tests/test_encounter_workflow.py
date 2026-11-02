"""Regression tests for action-linked ammo and encounter resolution."""
import unittest

from ctp500_app.characters import Character, EquipmentSelection
from ctp500_app.encounter_workflow import change_weapon_ammo, weapon_ammo
from ctp500_app.encounters import Combatant, EncounterSession, RulesEngine
from ctp500_app.ui import GearPrinterApp


def equipment(identity, name, category, *, ammo="", burst="1", quantity=1):
    fields = {"Name": name}
    if category == "Weapons":
        fields.update({"Ammo": ammo, "Burst": burst, "Effective Rank": "6", "Form Factor": "Ranged"})
    return EquipmentSelection(
        id=identity, category=category, source_gid=1, source_row=1, name=name,
        manufacturer="Test", fields=fields, base_price=0.0, adjusted_price=0.0,
        quantity=quantity,
    )


def combatant(*items):
    character = Character(
        id="character-1", name="Tester", race="Human", role="Generalist",
        power_level=8, seed=1, catalog_digest="", budget=10000, spent=0,
        base_abilities={key: 0 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
        defense_training={key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
        equipment=list(items), affinities=[], corruption_penalties=[], settings={},
        created_at="", updated_at="",
    )
    return Combatant.from_character(character)


def action_named(actor, name):
    return next(action for action in actor.actions if action.name == name)


class WeaponAmmoTests(unittest.TestCase):
    def test_each_weapon_shows_its_own_type_and_stock(self):
        actor = combatant(
            equipment("gun-a", "Cannonade", "Weapons", ammo="84mm Rocket"),
            equipment("gun-b", "Sidearm", "Weapons", ammo="9x19mm", burst="2"),
            equipment("ammo-a", "84mm Rocket", "Inventory", quantity=3),
            equipment("ammo-b", "9mm Cartridge", "Inventory", quantity=12),
        )
        cannon = weapon_ammo(actor, action_named(actor, "Cannonade"))
        sidearm = weapon_ammo(actor, action_named(actor, "Sidearm"))
        self.assertEqual((cannon.name, cannon.available, cannon.per_attack), ("84mm Rocket", 3, 1))
        self.assertEqual((sidearm.name, sidearm.available, sidearm.per_attack), ("9x19mm", 12, 2))

    def test_empty_weapon_can_be_restocked_from_action(self):
        actor = combatant(equipment("gun-a", "Cannonade", "Weapons", ammo="84mm Rocket"))
        gun = action_named(actor, "Cannonade")
        self.assertTrue(weapon_ammo(actor, gun).empty)
        change_weapon_ammo(actor, gun, 5)
        updated = action_named(actor, "Cannonade")
        self.assertEqual(weapon_ammo(actor, updated).available, 5)
        self.assertEqual(len([item for item in actor.character().equipment if item.category == "Inventory"]), 1)
        change_weapon_ammo(actor, updated, -2)
        self.assertEqual(weapon_ammo(actor, action_named(actor, "Cannonade")).available, 3)

    def test_adjustment_does_not_change_other_ammo(self):
        actor = combatant(
            equipment("gun-a", "Cannonade", "Weapons", ammo="84mm Rocket"),
            equipment("gun-b", "Sidearm", "Weapons", ammo="9x19mm"),
            equipment("ammo-b", "9mm Cartridge", "Inventory", quantity=8),
        )
        change_weapon_ammo(actor, action_named(actor, "Cannonade"), 1)
        self.assertEqual(weapon_ammo(actor, action_named(actor, "Cannonade")).available, 1)
        self.assertEqual(weapon_ammo(actor, action_named(actor, "Sidearm")).available, 8)

    def test_untracked_weapon_still_displays_ammo_type(self):
        actor = combatant(equipment("gun", "Test Rifle", "Weapons", ammo="9x19mm", burst=""))
        info = weapon_ammo(actor, action_named(actor, "Test Rifle"))
        self.assertEqual(info.name, "9x19mm")
        self.assertFalse(info.tracked)
        self.assertIn("untracked", info.summary)

    def test_weapon_with_no_ammo_field_has_no_counter(self):
        actor = combatant(equipment("blade", "Sword", "Weapons", ammo="-"))
        self.assertIsNone(weapon_ammo(actor, action_named(actor, "Sword")))

    def test_enter_commits_miss_preview_instead_of_rerolling(self):
        app = GearPrinterApp.__new__(GearPrinterApp)
        app._encounter_resolution_preview = {"resistance": None}
        calls = []
        app._encounter_apply_action_preview = lambda: calls.append("apply")
        app._encounter_preview_action = lambda: calls.append("preview")

        app._encounter_resolve_action()

        self.assertEqual(calls, ["apply"])

    def test_missed_attack_spends_ammo_and_action_economy(self):
        attacker = combatant(
            equipment("gun", "Cannonade", "Weapons", ammo="84mm Rocket"),
            equipment("ammo", "84mm Rocket", "Inventory", quantity=2),
        )
        target = Combatant.ad_hoc("Defender", 7)
        session = EncounterSession(combatants=[attacker, target])
        action = action_named(attacker, "Cannonade")
        app = GearPrinterApp.__new__(GearPrinterApp)
        app._encounter_resolution_preview = {
            "attacker_id": attacker.id, "target_id": target.id, "action_id": action.id,
            "attack": {"hit": False, "total": 3, "defense": 15},
            "resistance": None, "condition_triggers": {},
        }
        app._encounter_preview_entities = lambda: (attacker, target, action)
        logged = []
        def mutate(kind, summary, operation, **kwargs):
            operation(session)
            logged.append((kind, summary))
        app._encounter_mutate = mutate

        app._encounter_apply_action_preview()

        self.assertEqual(weapon_ammo(attacker, action_named(attacker, "Cannonade")).available, 1)
        self.assertTrue(attacker.standard_used)
        self.assertEqual(target.bruises, 0)
        self.assertIn("Missed", logged[0][1])
        self.assertIn("used 1 84mm Rocket", logged[0][1])


if __name__ == "__main__":
    unittest.main()
