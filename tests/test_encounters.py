from __future__ import annotations

import random
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from ctp500_app.character_store import CharacterStore
from ctp500_app.characters import Character, EquipmentSelection, GeneratorSettings
from ctp500_app.encounter_store import EncounterStore
from ctp500_app.encounters import (
    DAMAGE_TYPE_RULES,
    ActionCard,
    Campaign,
    Combatant,
    ConditionInstance,
    EncounterController,
    EncounterBuilder,
    EncounterSession,
    EncounterTemplate,
    LootEntry,
    Objective,
    RecipeSlot,
    RuntimeModifier,
    RulesEngine,
    action_document,
    combatant_document,
    condition_document,
    initiative_document,
    loot_document,
    target_threat,
    threat_score,
)
from ctp500_app.models import CatalogSnapshot


def character(name: str = "Hero", pl: int = 10) -> Character:
    now = datetime.now().astimezone().isoformat()
    return Character(
        f"id-{name}", name, "Human", "Generalist", pl, 1, "digest", 1000, 0,
        {"STR": 2, "AGL": 3, "FGT": 4, "AWE": 2, "STA": 3, "DEX": 5, "INT": 1, "PRE": 0},
        {"DODGE": 2, "PARRY": 2, "FORT": 2, "TOUGH": 2, "WILL": 2},
        [EquipmentSelection("gun", "Weapons", 1, 1, "Blaster", "Acme", {"Rank": "8", "Form Factor": "Pistol"}, 100, 100)],
        [], [], {}, now, now,
    )


class EncounterStoreTests(unittest.TestCase):
    def test_migration_preserves_characters_and_round_trips_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "characters.db"
            character_store = CharacterStore(path)
            source = character()
            character_store.save(source)
            store = EncounterStore(path)
            campaign = Campaign(name="Test Campaign")
            template = EncounterTemplate(campaign_id=campaign.id, name="Ambush", combatants=[Combatant.from_character(source)])
            session = EncounterSession.from_template(template)
            store.save_campaign(campaign)
            store.save_template(template)
            store.save_session(session)

            self.assertEqual(character_store.get(source.id).name, source.name)
            self.assertEqual(store.get_campaign(campaign.id).name, campaign.name)
            self.assertEqual(store.get_template(template.id).combatants[0].name, source.name)
            self.assertEqual(store.get_session(session.id).template_id, template.id)

    def test_event_commit_undo_redo_and_branching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EncounterStore(Path(directory) / "encounters.db")
            actor = Combatant.ad_hoc("Thug", 5)
            session = EncounterSession(name="Fight", combatants=[actor])
            store.save_session(session)
            controller = EncounterController(session, store)
            controller.add_bruise(actor.id)
            controller.add_condition(actor.id, ConditionInstance(name="Dazed"))
            self.assertEqual(controller.session.combatants[0].bruises, 1)
            self.assertTrue(controller.undo())
            self.assertEqual(controller.session.combatants[0].conditions, [])
            self.assertTrue(controller.undo())
            self.assertEqual(controller.session.combatants[0].bruises, 0)
            self.assertTrue(controller.redo())
            self.assertEqual(controller.session.combatants[0].bruises, 1)
            controller.add_bruise(actor.id)
            self.assertFalse(controller.redo())
            self.assertEqual(len(store.list_events(session.id)), 2)

    def test_action_library_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EncounterStore(Path(directory) / "encounters.db")
            action = ActionCard(name="Mind Blast", effect_type="Affliction", rank=8)
            store.save_action("hero", action)
            self.assertEqual(store.list_actions("hero")[0].resistance_dc, 18)
            self.assertTrue(store.delete_action("hero", action.id))

    def test_turn_and_forced_ticks_are_persisted_and_undoable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EncounterStore(Path(directory) / "encounters.db")
            first = Combatant.ad_hoc("First", 5)
            second = Combatant.ad_hoc("Second", 5)
            first.initiative, second.initiative = 20, 10
            bleeding = ConditionInstance(name="Bleeding", duration_mode="custom")
            second.conditions.append(bleeding)
            session = EncounterSession(combatants=[first, second])
            store.save_session(session)
            controller = EncounterController(session, store)

            controller.next_turn({bleeding.id: {"save": 1}})
            self.assertEqual(second.bruises, 1)
            turn_event = store.list_events(session.id)[-1]
            self.assertEqual(turn_event.dice["condition_ticks"][0]["save"]["dc"], 10)

            controller.force_condition_tick(second.id, bleeding.id, {"save": 1})
            self.assertEqual(second.bruises, 2)
            self.assertTrue(controller.undo())
            self.assertEqual(controller.session.combatants[1].bruises, 1)

    def test_deleting_campaign_unfiles_embedded_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EncounterStore(Path(directory) / "encounters.db")
            campaign = Campaign(name="Disposable")
            template = EncounterTemplate(campaign_id=campaign.id, name="Template")
            session = EncounterSession(campaign_id=campaign.id, name="Session")
            store.save_campaign(campaign)
            store.save_template(template)
            store.save_session(session)
            store.delete_campaign(campaign.id)
            self.assertEqual(store.get_template(template.id).campaign_id, "")
            self.assertEqual(store.get_session(session.id).campaign_id, "")


class EncounterRulesTests(unittest.TestCase):
    @staticmethod
    def protected_combatant(*, armor_text: str = "Steel plate") -> Combatant:
        source = character("Target")
        source.equipment.extend((
            EquipmentSelection("shield", "Armor", 1, 2, "Defense Shield", "VoxShield", {"Coverage": "Shield"}, 0, 0, modifiers={"TOUGH": 3}),
            EquipmentSelection("armor", "Armor", 1, 3, armor_text, "Armor Co", {"Coverage": "Body"}, 0, 0, modifiers={"TOUGH": 4}),
        ))
        return Combatant.from_character(source)

    def test_damage_chart_contains_all_fifteen_types(self) -> None:
        self.assertEqual(
            tuple(DAMAGE_TYPE_RULES),
            ("Slug", "Laser", "Plasma", "Arc", "Fire", "Rad", "Cryo", "Acid", "Poison", "Disease", "Sonic", "Gravity", "Void", "EMP", "Psychic"),
        )

    def test_weapon_ammo_and_single_use_consumables_derive_and_consume(self) -> None:
        source = character("Supplied")
        source.equipment = [
            EquipmentSelection("gun", "Weapons", 1, 1, "Burst Pistol", "Acme", {
                "Rank": "5", "Form Factor": "Pistol", "Ammo": "9mm Cartridge", "Burst": "3", "Clip": "12",
            }, 100, 100),
            EquipmentSelection("ammo", "Inventory", 2, 1, "9mm Cartridge", "All", {
                "Type": "AMMO", "Effect": "Ammunition",
            }, 1, 1, quantity=2),
            EquipmentSelection("drug", "Inventory", 2, 2, "Combat Stim", "All", {
                "Type": "DRUGS", "Effect": "Enhanced Strength 2",
            }, 10, 10),
            EquipmentSelection("rope", "Inventory", 2, 3, "Rope", "All", {
                "Type": "MILITARY EQUIPMENT", "Effect": "Useful",
            }, 5, 5),
        ]
        actor = Combatant.from_character(source)
        actions = {action.name: action for action in actor.actions}

        self.assertIn("Burst Pistol", actions)
        self.assertIn("Combat Stim", actions)
        self.assertNotIn("Rope", actions)
        self.assertEqual(RulesEngine.action_resource_status(actor, actions["Burst Pistol"]), (2, 3))
        self.assertEqual(RulesEngine.action_resource_status(actor, actions["Combat Stim"]), (1, 1))

        RulesEngine.consume_action_resource(actor, actions["Burst Pistol"])
        self.assertEqual(RulesEngine.action_resource_status(actor, actions["Burst Pistol"]), (0, 3))
        RulesEngine.consume_action_resource(actor, next(action for action in actor.actions if action.name == "Combat Stim"))
        self.assertNotIn("Combat Stim", {action.name for action in actor.actions})

    def test_weapon_without_burst_is_unlimited_even_when_ammo_is_named(self) -> None:
        source = character("Unlimited")
        source.equipment[0].fields.update({"Ammo": "Energy Cell", "Clip": "20"})
        actor = Combatant.from_character(source)
        weapon = next(action for action in actor.actions if action.name == "Blaster")
        self.assertEqual(RulesEngine.action_resource_status(actor, weapon), (0, 0))

    def test_incoming_damage_layers_ignore_resist_block_and_destroy(self) -> None:
        target = self.protected_combatant()

        laser = RulesEngine.incoming_damage_result(target, "Laser", 10, 10)
        self.assertEqual(laser["blocked_by"], "Shield")
        self.assertIsNone(laser["resistance"])

        slug = RulesEngine.incoming_damage_result(target, "Slug", 10, 10)
        self.assertEqual(slug["excluded_toughness"], 3)
        self.assertEqual(slug["resistance"]["modifier"], 9)

        void = RulesEngine.incoming_damage_result(target, "Void", 10, 1, random.Random(2))
        self.assertEqual(void["destroyed"], ["Shield", "Armor"])
        self.assertEqual(void["resistance"]["modifier"], 5)
        RulesEngine.apply_incoming_damage(target, void, 1)
        self.assertTrue({"shield destroyed", "armor destroyed"} <= RulesEngine.active_conditions(target))
        self.assertEqual(RulesEngine.stats(target)[1]["TOUGH"], 4)

    def test_live_toughness_breakdown_applies_damage_and_destroyed_layers(self) -> None:
        target = self.protected_combatant()
        target.bruises = 1
        target.modifiers.append(RuntimeModifier(target="TOUGH", amount=-2, source="Weakness"))
        _abilities, defenses, _speed = RulesEngine.stats(target)
        breakdown = RulesEngine.toughness_breakdown(target, defenses["TOUGH"])
        self.assertEqual(breakdown.text(), "Tough:2 +A:6 +S:5 +A+S:9")

        target.modifiers.append(RuntimeModifier(target="TOUGH", amount=-4, source="Armor Destroyed"))
        _abilities, defenses, _speed = RulesEngine.stats(target)
        breakdown = RulesEngine.toughness_breakdown(target, defenses["TOUGH"])
        self.assertEqual(breakdown.text(), "Tough:2 +S:5")

        document = combatant_document(EncounterSession(combatants=[target]), target)
        self.assertIn("Tough:2 +S:5", document.text)

    def test_special_armor_traits_control_arc_poison_and_emp(self) -> None:
        arc_target = self.protected_combatant(armor_text="Non-conductive ceramic plate")
        arc = RulesEngine.incoming_damage_result(arc_target, "Arc", 8, 10)
        self.assertEqual(arc["blocked_by"], "Armor")
        self.assertEqual(arc["destroyed"], ["Shield"])

        poison_target = self.protected_combatant(armor_text="Sealed hazmat armor")
        poison = RulesEngine.incoming_damage_result(poison_target, "Poison", 8, 10)
        self.assertEqual(poison["blocked_by"], "Armor")

        organic = Combatant.from_character(character("Organic"))
        emp = RulesEngine.incoming_damage_result(organic, "EMP", 8, 10)
        self.assertEqual(emp["blocked_by"], "No electronic target")

        shielded_organic = self.protected_combatant()
        emp_shield = RulesEngine.incoming_damage_result(shielded_organic, "EMP", 8, 10)
        self.assertEqual(emp_shield["destroyed"], ["Shield"])
        self.assertEqual(emp_shield["blocked_by"], "No electronic target")

    def test_secondary_and_continuous_chart_effects_are_applied(self) -> None:
        fire_target = Combatant.from_character(character("Burning"))
        fire = RulesEngine.incoming_damage_result(fire_target, "Fire", 10, 1, random.Random(1))
        self.assertEqual(fire["secondary"], "Ignited")
        RulesEngine.apply_incoming_damage(fire_target, fire, 2)
        self.assertIn("ignited", RulesEngine.active_conditions(fire_target))

        acid_target = Combatant.from_character(character("Corroded"))
        acid = RulesEngine.incoming_damage_result(acid_target, "Acid", 10, 1)
        RulesEngine.apply_incoming_damage(acid_target, acid, 2)
        self.assertIn("acid burning", RulesEngine.active_conditions(acid_target))

    def test_encounter_generation_inherits_character_race_profile(self) -> None:
        class CapturingGenerator:
            def __init__(self) -> None:
                self.settings = []

            def generate(self, _snapshot, settings, name="", seed=None):
                self.settings.append(settings)
                return character(name or "Generated", settings.power_level)

        generator = CapturingGenerator()
        builder = EncounterBuilder(generator)
        profile = GeneratorSettings(
            race="Random",
            race_enabled={"Human": True, "Elf": False},
            race_weights={"Human": 7.0, "Elf": 0.0},
            parent_enabled={"Human": True, "Elf": False},
            parent_weights={"Human": 4.0, "Elf": 0.0},
        )
        snapshot = CatalogSnapshot((), datetime.now().astimezone(), "empty")
        builder.quick_generate(snapshot, party_size=1, party_power_level=5, difficulty="Easy", seed=3, base_settings=profile)
        template = EncounterTemplate(recipe_slots=[RecipeSlot(name="Recipe enemy", settings={"power_level": 6, "race": "Random"})])
        builder.build_recipe(template, snapshot, {}, seed=4, base_settings=profile)

        self.assertTrue(generator.settings)
        for settings in generator.settings:
            self.assertEqual(settings.race_enabled, profile.race_enabled)
            self.assertEqual(settings.race_weights, profile.race_weights)
            self.assertEqual(settings.parent_enabled, profile.parent_enabled)
            self.assertEqual(settings.parent_weights, profile.parent_weights)

    def test_encounter_weapon_guarantee_overrides_recipe_slot_setting(self) -> None:
        class CapturingGenerator:
            def __init__(self) -> None:
                self.settings = []

            def generate(self, _snapshot, settings, name="", seed=None):
                self.settings.append(settings)
                return character(name or "Generated", settings.power_level)

        generator = CapturingGenerator()
        builder = EncounterBuilder(generator)
        profile = GeneratorSettings(guarantee_weapon=True)
        template = EncounterTemplate(recipe_slots=[RecipeSlot(name="Recipe enemy", settings={"guarantee_weapon": False})])
        builder.build_recipe(template, CatalogSnapshot((), datetime.now().astimezone(), "empty"), {}, base_settings=profile)
        self.assertTrue(generator.settings[0].guarantee_weapon)

    def test_threat_formula_and_targets(self) -> None:
        self.assertEqual(threat_score(10, 10), 1)
        self.assertEqual(threat_score(12, 10), 2)
        self.assertEqual(threat_score(10, 10, minion=True), .25)
        self.assertEqual(target_threat(4, "Severe"), 8)

    def test_damage_degrees_bruises_stagger_and_minions(self) -> None:
        target = Combatant.ad_hoc("Target", 10)
        target.ad_hoc_defenses["TOUGH"] = 10
        action = ActionCard(name="Blast", rank=10)
        one = RulesEngine.resistance_result(target, action, die=14)  # 24 vs 25
        three = RulesEngine.resistance_result(target, action, die=4)  # 14 vs 25
        self.assertEqual(one["suggestions"], ["Bruise +1"])
        self.assertIn("Staggered", three["suggestions"])
        RulesEngine.apply_resistance(target, action, three, 1)
        self.assertEqual(target.bruises, 1)
        self.assertIn("staggered", RulesEngine.active_conditions(target))
        repeated = RulesEngine.resistance_result(target, action, die=4)
        self.assertIn("Incapacitated", repeated["suggestions"])
        minion = Combatant.ad_hoc("Minion", 10, minion=True)
        minion.ad_hoc_defenses["TOUGH"] = 10
        result = RulesEngine.resistance_result(minion, action, die=14)
        self.assertEqual(result["degree"], 4)

    def test_bruises_and_conditions_modify_live_stats(self) -> None:
        actor = Combatant.from_character(character())
        actor.bruises = 2
        actor.conditions.extend([ConditionInstance(name="Vulnerable"), ConditionInstance(name="Impaired")])
        _abilities, defenses, _speed = RulesEngine.stats(actor)
        self.assertEqual(defenses["TOUGH"], 3)
        self.assertEqual(defenses["DODGE"], 2)
        self.assertEqual(RulesEngine.check_penalty(actor), -2)

    def test_attack_targets_ten_plus_active_defense(self) -> None:
        attacker = Combatant.ad_hoc("Attacker", 10)
        target = Combatant.ad_hoc("Target", 10)
        action = ActionCard(name="Strike", fixed_attack=10, target_defense="PARRY")
        result = RulesEngine.attack_result(attacker, target, action, die=10)
        self.assertEqual(result["defense"], 20)
        self.assertTrue(result["hit"])

    def test_unarmed_action_uses_final_strength_and_close_attack(self) -> None:
        source = character("Brawler")
        source.equipment.append(EquipmentSelection(
            "frame", "Bionics", 1, 2, "Reinforced Endo-Frame", "Cyber Co",
            {"Rank": "2", "Effects": "Enhanced Strength 1, Damage +1 with unarmed strikes"},
            0, 0, modifiers={"STR": 1},
        ))
        actor = Combatant.from_character(source)
        unarmed = next(action for action in actor.actions if action.source == "Intrinsic:Unarmed")
        self.assertEqual(unarmed.rank, 4)  # Base STR 2 + enhanced STR 1 + strike Damage 1.
        self.assertEqual(unarmed.fixed_attack, 4)
        self.assertEqual(unarmed.resistance_dc, 19)

    def test_bionic_attacks_afflictions_and_special_actions_are_derived(self) -> None:
        source = character("Cyborg")
        source.equipment.extend((
            EquipmentSelection("arc", "Bionics", 1, 4, "Arc Projector", "Cyber Co", {"Effects": "Ranged Damage 4 Linked Affliction 4 (Dazed, Stunned, Incapacitated; Fortitude)"}, 0, 0),
            EquipmentSelection("legs", "Bionics", 1, 3, "Jump Legs", "Cyber Co", {"Effects": "Leaping 3"}, 0, 0),
        ))
        actions = Combatant.from_character(source).actions
        damage = next(action for action in actions if action.name == "Arc Projector — Damage")
        affliction = next(action for action in actions if action.name == "Arc Projector — Affliction")
        special = next(action for action in actions if action.name == "Jump Legs")
        self.assertEqual((damage.rank, damage.range, damage.fixed_attack), (4, "Ranged", 5))
        self.assertEqual(affliction.degree_conditions["2"], ["Stunned"])
        self.assertEqual(special.effect_type, "Special")

    def test_ad_hoc_combatants_always_have_unarmed_action(self) -> None:
        actor = Combatant.ad_hoc("Extra", 5)
        self.assertEqual([(action.name, action.rank) for action in actor.actions], [("Unarmed", 0)])

    def test_headless_hit_check_uses_selected_active_defense(self) -> None:
        target = Combatant.ad_hoc("Target", 5)
        target.ad_hoc_defenses["DODGE"] = 7
        target.ad_hoc_defenses["PARRY"] = 4
        target.conditions.append(ConditionInstance(name="Vulnerable"))

        dodge = RulesEngine.headless_attack_result(target, 13, "DODGE")
        parry = RulesEngine.headless_attack_result(target, 13, "PARRY")

        self.assertEqual(dodge["defense"], 13)
        self.assertTrue(dodge["hit"])
        self.assertEqual(parry["defense"], 12)
        with self.assertRaises(ValueError):
            RulesEngine.headless_attack_result(target, 20, "TOUGH")

    def test_group_initiative_turns_and_timers(self) -> None:
        one = Combatant.ad_hoc("Minion 1", 5, minion=True)
        two = Combatant.ad_hoc("Minion 2", 5, minion=True)
        one.initiative_group = two.initiative_group = "Thugs"
        session = EncounterSession(combatants=[one, two])
        controller = EncounterController(session)
        controller.roll_initiative(random.Random(2))
        self.assertEqual(one.initiative, two.initiative)
        controller.next_turn()
        controller.next_turn()
        self.assertEqual(session.round, 2)

    def test_hazard_can_take_an_initiative_turn(self) -> None:
        actor = Combatant.ad_hoc("Hero", 5)
        actor.initiative = 10
        hazard = Objective(name="Laser grid", kind="Hazard", initiative=20)
        session = EncounterSession(combatants=[actor], objectives=[hazard])
        self.assertIs(session.current_entity(), hazard)
        EncounterController(session).next_turn()
        self.assertIs(session.current_entity(), actor)

    def test_statless_marker_takes_a_normal_initiative_turn(self) -> None:
        actor = Combatant.ad_hoc("Enemy", 5)
        actor.initiative = 10
        marker = Objective(name="Players", kind="Marker", initiative=15, target=1)
        session = EncounterSession(combatants=[actor], objectives=[marker])
        self.assertEqual([entry.name for entry in session.ordered_turn_entities()], ["Players", "Enemy"])
        controller = EncounterController(session)
        controller.next_turn()
        self.assertIs(session.current_entity(), actor)
        controller.next_turn()
        self.assertEqual(session.round, 2)

    def test_defeat_reveals_staged_loot_and_documents_render_text(self) -> None:
        actor = Combatant.from_character(character("Bandit"))
        session = EncounterSession(name="Raid", combatants=[actor], loot=[LootEntry(name="Chip", source_combatant_id=actor.id, credits=20)])
        controller = EncounterController(session)
        controller.mark_defeated(actor.id)
        self.assertTrue(session.loot[0].revealed)
        self.assertIn("20 cr", loot_document(session).text)
        actor.initiative = 15
        self.assertIn("Bandit", initiative_document(session).text)

    def test_action_and_condition_print_documents(self) -> None:
        actor = Combatant.ad_hoc("Mage", 8)
        action = ActionCard(name="Hex", effect_type="Affliction", rank=8)
        condition = ConditionInstance(name="Impaired", source="Hex", recovery_defense="WILL", dc=18)
        self.assertIn("DC 18", action_document(actor, action).text)
        self.assertIn("WILL vs DC 18", condition_document(actor, condition).text)

    def test_weapon_notes_create_structured_condition_triggers_and_keep_element(self) -> None:
        source = character("Firestarter")
        source.equipment[0].fields.update({"Element": "Fire", "Special Notes": "25% chance of Burning"})
        action = next(entry for entry in Combatant.from_character(source).actions if entry.name == "Blaster")
        self.assertEqual(action.descriptors, ["Fire"])
        self.assertEqual(action.condition_triggers, {"Burning": .25})

    def test_source_condition_chance_format_handles_wrapping_and_unrelated_percentages(self) -> None:
        notes = (
            "10% catastrophic failure, 25% chance of Burning, "
            "50 % chance\n of Poisoned, 75% CHANCE OF DISEASED, 12.5% chance of Bleeding"
        )
        self.assertEqual(RulesEngine.condition_triggers_from_notes(notes), {
            "Burning": .25, "Poisoned": .5, "Diseased": .75, "Bleeding": .125,
        })
        self.assertEqual(
            RulesEngine.condition_triggers_from_notes("25% failure chance; Affliction (Burning)"),
            {"Burning": 1.0},
        )

    def test_burning_ticks_half_rank_then_expires_and_reapplication_resets(self) -> None:
        target = Combatant.ad_hoc("Target", 5)
        burning = ConditionInstance(name="Burning", original_rank=9, rounds_remaining=1, duration_mode="custom")
        target.conditions.append(burning)
        result = RulesEngine.tick_condition(target, burning, 1, {"damage": 20})
        self.assertEqual(result["half_rank"], 4)
        self.assertFalse(burning.active)

        burning.active = True
        action = ActionCard(name="Torch", rank=12, condition_triggers={"Burning": .01})
        trigger = RulesEngine.condition_trigger_results(target, action, random.Random(2))
        self.assertTrue(trigger["Burning"]["reset"])
        RulesEngine.apply_condition_triggers(target, action, trigger, 2)
        self.assertEqual((burning.rounds_remaining, burning.original_rank), (6, 12))

    def test_poison_progression_uses_escalating_fortitude_and_one_stage_per_failure(self) -> None:
        target = Combatant.ad_hoc("Target", 5)
        target.ad_hoc_defenses.update({"TOUGH": 20, "FORT": 0})
        poisoned = ConditionInstance(name="Poisoned", original_rank=8, rounds_remaining=6, duration_mode="custom")
        target.conditions.append(poisoned)

        first = RulesEngine.tick_condition(target, poisoned, 1, {"damage": 20, "progression": 20})
        self.assertTrue(first["progression"]["success"])
        self.assertEqual(poisoned.stage, 0)
        second = RulesEngine.tick_condition(target, poisoned, 2, {"damage": 20, "progression": 1})
        self.assertEqual(second["progression"]["dc"], 11)
        self.assertEqual(poisoned.stage, 1)
        self.assertIsNotNone(RulesEngine.active_condition(target, "Hindered"))
        RulesEngine.tick_condition(target, poisoned, 3, {"damage": 20, "progression": 1})
        self.assertEqual(poisoned.stage, 2)
        self.assertIsNone(RulesEngine.active_condition(target, "Hindered"))
        self.assertIsNotNone(RulesEngine.active_condition(target, "Immobile"))

    def test_bleeding_and_dying_use_stamina_and_escalate_dc(self) -> None:
        target = Combatant.ad_hoc("Target", 5)
        target.ad_hoc_abilities["STA"] = 2
        bleeding = ConditionInstance(name="Bleeding", duration_mode="custom", dc=10)
        target.conditions.append(bleeding)
        success = RulesEngine.tick_condition(target, bleeding, 1, {"save": 8})
        self.assertTrue(success["save"]["success"])
        failure = RulesEngine.tick_condition(target, bleeding, 1, {"save": 1})
        self.assertEqual(failure["save"]["dc"], 11)
        self.assertEqual(target.bruises, 1)

        dying = ConditionInstance(name="Dying", duration_mode="custom", dc=10)
        target.conditions.append(dying)
        dead = RulesEngine.tick_condition(target, dying, 1, {"save": 1})
        self.assertTrue(dead["dead"])
        self.assertTrue(target.defeated)
        self.assertIsNotNone(RulesEngine.active_condition(target, "Dead"))

    def test_immortality_prevents_dead_from_marking_defeated(self) -> None:
        target = Combatant.ad_hoc("Deathless", 5)
        target.actions.append(ActionCard(name="Return", effect_type="Special", notes="Immortality 5"))
        dying = ConditionInstance(name="Dying", duration_mode="custom")
        target.conditions.append(dying)
        result = RulesEngine.tick_condition(target, dying, 1, {"save": 1})
        self.assertTrue(result["immortal"])
        self.assertFalse(target.defeated)
        self.assertIsNotNone(RulesEngine.active_condition(target, "Dead"))

    def test_disease_weakening_is_cumulative_and_persistent_healing_restores_all(self) -> None:
        target = Combatant.ad_hoc("Patient", 5)
        disease = ConditionInstance(name="Diseased", duration_mode="custom")
        target.conditions.append(disease)
        RulesEngine.tick_condition(target, disease, 1, rng=random.Random(1))
        RulesEngine.tick_condition(target, disease, 2, rng=random.Random(2))
        self.assertEqual(len(disease.modifier_ids), 2)
        before = RulesEngine.stats(target)[0]
        healing = ActionCard(name="Cure", effect_type="Healing", persistent=True)
        result = RulesEngine.apply_action_hit(target, healing, None, {}, 2)
        after = RulesEngine.stats(target)[0]
        self.assertEqual(result["diseased_cured"], 1)
        self.assertFalse(disease.active)
        self.assertEqual(sum(after.values()) - sum(before.values()), 2)

    def test_custom_condition_state_round_trips_without_schema_migration(self) -> None:
        condition = ConditionInstance(
            name="Poisoned", original_rank=9, rounds_remaining=4, saves_made=2,
            stage=1, modifier_ids=["m"], child_condition_ids=["c"],
        )
        action = ActionCard(name="Needler", condition_triggers={"Poisoned": .5}, persistent=True)
        restored_condition = ConditionInstance.from_dict(asdict(condition))
        restored_action = ActionCard.from_dict(asdict(action))
        self.assertEqual((restored_condition.original_rank, restored_condition.saves_made, restored_condition.child_condition_ids), (9, 2, ["c"]))
        self.assertEqual((restored_action.condition_triggers, restored_action.persistent), ({"Poisoned": .5}, True))


if __name__ == "__main__":
    unittest.main()
