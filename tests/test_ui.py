from __future__ import annotations

import unittest
import inspect
from datetime import datetime

from ctp500_app.catalog import parse_category_csv
from ctp500_app.ui import (
    ENCOUNTER_HOTKEYS,
    GearPrinterApp,
    GENERAL_COLUMNS,
    TOUGHNESS_COLORS,
    catalog_columns,
    catalog_value,
    stretch_catalog_column,
)
from ctp500_app.characters import Character, CharacterGenerator, EquipmentSelection, GeneratorSettings
from ctp500_app.encounters import ActionCard, Combatant, ConditionInstance, EncounterSession, Objective, ResourceCounter, RuntimeModifier
from ctp500_app.models import CatalogSnapshot


class CatalogTableTests(unittest.TestCase):
    def test_general_columns_include_corruption_and_capacity(self) -> None:
        self.assertEqual(GENERAL_COLUMNS[-2:], ("Corruption", "Capacity"))

    def test_general_columns_place_special_notes_between_rank_and_price(self) -> None:
        rank_index = GENERAL_COLUMNS.index("Rank")
        self.assertEqual(
            GENERAL_COLUMNS[rank_index : rank_index + 3],
            ("Rank", "Special Notes", "Price"),
        )

    def test_specific_category_uses_source_sheet_columns(self) -> None:
        item = parse_category_csv(
            "Weapons",
            1,
            b"Manufacturer,Name,Element,Clip,Price\nAcme,Arc Gun,Arc,6,25\n",
        )[0]

        self.assertEqual(
            catalog_columns((item,), "Weapons"),
            ("Manufacturer", "Name", "Element", "Clip", "Price"),
        )
        self.assertEqual(catalog_value(item, "Element", "Weapons"), "Arc")

    def test_general_capacity_accepts_current_cap_header(self) -> None:
        item = parse_category_csv(
            "Bionics",
            2,
            b"Manufacturer,Name,Type,Corruption,Cap,Price\nAcme,Eye,Optic,2,4,100\n",
        )[0]

        self.assertEqual(catalog_value(item, "Corruption", "All"), "2")
        self.assertEqual(catalog_value(item, "Capacity", "All"), "4")

    def test_general_special_notes_uses_source_field(self) -> None:
        item = parse_category_csv(
            "Weapons",
            3,
            b"Manufacturer,Name,Special Notes,Price\nAcme,Arc Gun,Two handed,25\n",
        )[0]

        self.assertEqual(catalog_value(item, "Special Notes", "All"), "Two handed")

    def test_only_rightmost_column_absorbs_spare_width(self) -> None:
        columns = ("Category", "Name", "Manufacturer", "Capacity")

        self.assertFalse(stretch_catalog_column("Category", columns))
        self.assertFalse(stretch_catalog_column("Name", columns))
        self.assertFalse(stretch_catalog_column("Manufacturer", columns))
        self.assertTrue(stretch_catalog_column("Capacity", columns))


class CharacterOrangeExpansionTests(unittest.TestCase):
    def _app(self):
        app = GearPrinterApp.__new__(GearPrinterApp)
        items = [
            EquipmentSelection("one", "Weapons", 1, 1, "One", "All", {}, 0, 0),
            EquipmentSelection("two", "Armor", 1, 2, "Two", "All", {}, 0, 0),
            EquipmentSelection("blue", "Inventory", 1, 3, "Blue", "All", {}, 0, 0),
        ]
        app.current_character = type("CharacterStub", (), {"equipment": items})()
        app.essential_character_items = {"one", "two"}
        app.expanded_character_items = {"blue"}
        app._refresh_character_view = lambda: None
        app.char_status_var = type("StatusStub", (), {"set": lambda self, value: None})()
        return app

    def test_mixed_orange_states_favor_opening_every_orange_item(self) -> None:
        app = self._app()
        app.expanded_character_items = {"one", "blue"}

        app._toggle_orange_character_items()

        self.assertEqual(app.expanded_character_items, {"one", "two", "blue"})

    def test_second_press_closes_all_orange_items(self) -> None:
        app = self._app()
        app.expanded_character_items = {"one", "two", "blue"}

        app._toggle_orange_character_items()

        self.assertEqual(app.expanded_character_items, {"blue"})

    def test_make_all_orange_marks_every_equipment_item(self) -> None:
        app = self._app()
        app.essential_character_items = {"one"}

        app._make_all_character_items_orange()

        self.assertEqual(app.essential_character_items, {"one", "two", "blue"})


class CharacterBudgetReferenceTests(unittest.TestCase):
    def test_clicking_budget_reference_sets_exact_amount(self) -> None:
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.char_budget_var = type(
            "BudgetStub", (), {"set": lambda self, value: setattr(self, "value", value)}
        )()

        result = app._set_character_budget(100_000)

        self.assertEqual(app.char_budget_var.value, 100_000)
        self.assertEqual(result, "break")


class EncounterHotkeyTests(unittest.TestCase):
    def _app(self, focus=None):
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.encounter_tab = "encounter"
        app.notebook = type("NotebookStub", (), {"select": lambda self: "encounter"})()
        app.root = type("RootStub", (), {"focus_get": lambda self: focus})()
        app.calls = []
        app._encounter_next_turn = lambda: app.calls.append("next")
        app._encounter_undo = lambda: app.calls.append("undo")
        app._encounter_bruise = lambda amount: app.calls.append(("bruise", amount))
        for name in (
            "_encounter_redo", "_encounter_roll_initiative", "_encounter_set_initiative",
            "_encounter_resolve_action", "_encounter_add_condition", "_encounter_toggle_defeated",
            "_encounter_reveal_loot", "_show_encounter_hotkeys",
        ):
            setattr(app, name, lambda: None)
        app._toggle_encounter_flag = lambda _field: None
        app._encounter_objective_progress = lambda _amount: None
        return app

    @staticmethod
    def _event(keysym, state=0):
        return type("EventStub", (), {"keysym": keysym, "state": state})()

    def test_common_hotkeys_dispatch_and_consume_event(self) -> None:
        app = self._app()
        self.assertEqual(app._encounter_hotkey(self._event("space")), "break")
        self.assertEqual(app._encounter_hotkey(self._event("B", 0x0001)), "break")
        self.assertEqual(app._encounter_hotkey(self._event("z", 0x0004)), "break")
        self.assertEqual(app.calls, ["next", ("bruise", -1), "undo"])

    def test_hotkeys_do_not_fire_while_typing(self) -> None:
        focus = type("FocusStub", (), {"winfo_class": lambda self: "TEntry"})()
        app = self._app(focus)
        self.assertIsNone(app._encounter_hotkey(self._event("space")))
        self.assertEqual(app.calls, [])

    def test_hotkeys_do_not_reenter_active_condition_roll_prompt(self) -> None:
        app = self._app()
        app._condition_roll_prompt_active = True
        self.assertIsNone(app._encounter_hotkey(self._event("space")))
        self.assertEqual(app.calls, [])

    def test_help_lists_core_shortcuts(self) -> None:
        shortcuts = {shortcut for shortcut, _description in ENCOUNTER_HOTKEYS}
        self.assertTrue({"Space", "Enter", "Ctrl+Z / Ctrl+Y"} <= shortcuts)


class EncounterConsoleTests(unittest.TestCase):
    def test_toughness_tags_use_layer_colors_without_becoming_interactive(self) -> None:
        class TextStub:
            def __init__(self):
                self.configured = {}
                self.bound = []

            def tag_add(self, tag, _start, _end):
                pass

            def tag_configure(self, tag, **values):
                self.configured[tag] = values

            def tag_bind(self, tag, event, _callback):
                self.bound.append((tag, event))

        app = GearPrinterApp.__new__(GearPrinterApp)
        app.encounter_stats_text = TextStub()
        app._encounter_text_tags = []
        app._encounter_sheet_tag_names = {}
        app._encounter_sheet_tag_targets = {}
        tags = [
            ("toughness-base", "base", 0, 7),
            ("toughness-armor", "armor", 8, 12),
            ("toughness-shield", "shield", 13, 17),
            ("toughness-both", "both", 18, 24),
        ]

        app._bind_encounter_sheet_tags(tags)

        colors = [values["foreground"] for values in app.encounter_stats_text.configured.values()]
        self.assertEqual(colors, ["#000000", "#d97706", "#1769aa", "#2e7d32"])
        self.assertEqual(list(TOUGHNESS_COLORS.values()), colors)
        self.assertTrue(all(not values["underline"] for values in app.encounter_stats_text.configured.values()))
        self.assertEqual(app.encounter_stats_text.bound, [])
        self.assertEqual(app._encounter_sheet_tag_targets, {})

    def test_campaign_selection_refreshes_only_dependent_lists(self) -> None:
        source = inspect.getsource(GearPrinterApp._on_encounter_campaign_selected)
        self.assertIn("_refresh_encounter_campaign_contents", source)
        self.assertNotIn("_refresh_encounter_library", source)

    class VarStub:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class WidgetStub:
        def __init__(self, selection=()):
            self._selection = selection
            self.options = {}

        def selection(self):
            return self._selection

        def configure(self, **options):
            self.options.update(options)

    def test_console_builder_contains_no_nested_notebook(self) -> None:
        source = inspect.getsource(GearPrinterApp._build_encounter_ui)
        self.assertNotIn("ttk.Notebook", source)
        self.assertIn("_build_encounter_tray", source)
        self.assertIn('bind("<Double-1>", self._encounter_roster_double_click)', source)
        self.assertIn('bind("<Button-1>", self._encounter_roster_target_click', source)
        self.assertIn('text="Weapons / Actions"', source)
        self.assertIn('tag_configure("heroic"', source)

    def test_incoming_damage_prompt_uses_one_form_for_all_inputs(self) -> None:
        source = inspect.getsource(GearPrinterApp._open_incoming_damage_prompt)
        self.assertIn("damage_type_var", source)
        self.assertIn("rank_var", source)
        self.assertIn("roll_var", source)
        self.assertEqual(source.count("tk.Toplevel"), 1)
        self.assertNotIn("simpledialog", source)

    def test_headless_attack_requires_hit_check_unless_shift_is_held(self) -> None:
        target = Combatant(name="Bandit")

        class RosterStub(self.WidgetStub):
            def identify_row(self, _y):
                return target.id

            def exists(self, row_id):
                return row_id == target.id

            def selection_set(self, row_id):
                self._selection = (row_id,)

        app = GearPrinterApp.__new__(GearPrinterApp)
        app.current_encounter = EncounterSession(combatants=[target])
        app.encounter_tree = RosterStub()
        calls = []
        app._encounter_headless_to_hit = lambda selected: calls.append(("hit", selected.id)) or "break"
        app._open_incoming_damage_prompt = lambda selected: calls.append(("damage", selected.id)) or "break"
        event = type("EventStub", (), {"y": 5, "state": 0})()

        app._encounter_incoming_damage(event)
        event.state = 0x0001
        app._encounter_incoming_damage(event)

        self.assertEqual(calls, [("hit", target.id), ("damage", target.id)])

    def test_hit_check_prompt_precedes_damage_prompt(self) -> None:
        source = inspect.getsource(GearPrinterApp._encounter_headless_to_hit)
        self.assertIn("Final to-hit total", source)
        self.assertIn("DODGE", source)
        self.assertIn("PARRY", source)
        self.assertIn("_open_incoming_damage_prompt", source)

    def test_attack_prompt_mousewheel_adjusts_numbers_and_choices(self) -> None:
        up = type("WheelEvent", (), {"delta": 120, "num": 0})()
        down = type("WheelEvent", (), {"delta": -120, "num": 0})()
        linux_up = type("WheelEvent", (), {"delta": 0, "num": 4})()
        number = self.VarStub("")
        choice = self.VarStub("DODGE")

        self.assertEqual(GearPrinterApp._adjust_prompt_number(number, up, minimum=1, maximum=20, default=10), "break")
        self.assertEqual(number.get(), "11")
        GearPrinterApp._adjust_prompt_number(number, down, minimum=1, maximum=20)
        self.assertEqual(number.get(), "10")
        number.set("20")
        GearPrinterApp._adjust_prompt_number(number, linux_up, minimum=1, maximum=20)
        self.assertEqual(number.get(), "20")

        GearPrinterApp._cycle_prompt_choice(choice, ("DODGE", "PARRY"), up)
        self.assertEqual(choice.get(), "PARRY")
        GearPrinterApp._cycle_prompt_choice(choice, ("DODGE", "PARRY"), down)
        self.assertEqual(choice.get(), "DODGE")

    def test_both_attack_prompts_bind_wheel_adjustment(self) -> None:
        hit_source = inspect.getsource(GearPrinterApp._encounter_headless_to_hit)
        damage_source = inspect.getsource(GearPrinterApp._open_incoming_damage_prompt)
        self.assertIn("_bind_prompt_wheel(defense_box", hit_source)
        self.assertIn("_bind_prompt_wheel(total_entry", hit_source)
        self.assertIn("_bind_prompt_wheel(damage_box", damage_source)
        self.assertIn("_bind_prompt_wheel(rank_entry", damage_source)
        self.assertIn("_bind_prompt_wheel(roll_entry", damage_source)

    def test_target_list_is_separate_and_excludes_actor_and_defeated(self) -> None:
        actor = Combatant(name="Hero", faction="Heroes")
        target = Combatant(name="Bandit", faction="Enemies")
        defeated = Combatant(name="Down", faction="Enemies", defeated=True)
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.current_encounter = EncounterSession(combatants=[actor, target, defeated])
        app.encounter_target_var = self.VarStub()
        app.encounter_target_box = self.WidgetStub()
        app._encounter_target_ids = {}
        app._encounter_resolution_preview = None
        app.encounter_resolve_status_var = self.VarStub()

        app._refresh_encounter_targets(actor)

        self.assertEqual(set(app._encounter_target_ids.values()), {target.id})
        self.assertEqual(app.encounter_target_var.get(), "Bandit — Enemies")

    def test_encounter_sheet_contains_full_character_and_tagged_runtime_state(self) -> None:
        now = datetime.now().astimezone().isoformat()
        gear = EquipmentSelection("blade", "Weapons", 1, 2, "Arc Blade", "Acme", {"Rank": "5", "Form Factor": "Melee", "Special Notes": "Sharp"}, 20, 20)
        ammo = EquipmentSelection("ammo", "Inventory", 2, 3, "Arc Cartridge", "Acme", {"Type": "AMMO"}, 8, 8, quantity=8)
        source = Character(
            "hero", "Hero", "Human", "Generalist", 5, 1, "digest", 100, 20,
            {key: 0 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
            {key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
            [gear, ammo], [], [], {}, now, now,
        )
        actor = Combatant.from_character(source)
        actor.conditions.append(ConditionInstance(name="Dazed"))
        actor.resources.append(ResourceCounter(name="Ammo", current=2, maximum=4))
        actor.modifiers.append(RuntimeModifier(target="TOUGH", amount=-1, source="Damage"))
        session = EncounterSession(combatants=[actor])
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.encounter_expanded_items = {}
        app.encounter_metadata_expanded = set()
        app.encounter_store = type("StoreStub", (), {"source_changed": lambda _self, _actor, _source: False})()
        app.character_store = type("CharactersStub", (), {"get": lambda _self, _identity: source})()

        collapsed, tags = app._encounter_sheet_text(actor, session)
        app.encounter_metadata_expanded.add(actor.id)
        app.encounter_expanded_items.setdefault(actor.id, set()).add(gear.id)
        expanded, _tags = app._encounter_sheet_text(actor, session)

        self.assertTrue({"ACTIONS", "CONDITIONS", "GEAR", "Character MetaData [Show]"} <= set(collapsed.splitlines()))
        self.assertFalse({"RESOURCES", "RUNTIME MODIFIERS", "CHARACTER SNAPSHOT"} & set(collapsed.splitlines()))
        self.assertTrue({"combatant", "action", "condition", "metadata"} <= {kind for kind, _identity, _start, _end in tags})
        self.assertIn(("gear", ammo.id), {(kind, identity) for kind, identity, _start, _end in _tags})
        self.assertIn("Arc Cartridge ×8", collapsed)
        self.assertEqual(collapsed.count("Arc Cartridge ×8"), 1)
        self.assertGreater(len(expanded), len(collapsed))
        self.assertIn("Sharp", expanded)

    def test_sheet_context_menus_cover_tag_actions(self) -> None:
        source = inspect.getsource(GearPrinterApp._encounter_tag_context_menu)
        for action in ("Resolve / preview", "Recover", "Increase", "Toggle modifier", "Remove gear", "Print item"):
            self.assertIn(action, source)

    def test_roster_context_menu_exposes_all_character_actions(self) -> None:
        app = GearPrinterApp.__new__(GearPrinterApp)
        entries = app._encounter_character_menu_entries(Combatant(name="Hero"), include_roster_actions=True)
        commands = {entry[0]: entry for entry in entries if entry is not None}

        for label in (
            "Incoming attack…", "Set initiative", "Bruise +", "Bruise -", "Add condition…",
            "New action…", "Add resource…", "Add gear…", "Toggle defeated", "Toggle delayed",
            "Toggle readied", "Toggle holding", "Toggle skipped", "Toggle standard used",
            "Toggle move used", "Toggle reaction used", "Refresh source", "Publish reusable actions",
            "Edit notes…", "Print combatant", "Remove",
        ):
            self.assertIn(label, commands)

        for label in ("Bruise +", "Bruise -", "Toggle defeated", "Toggle standard used", "Toggle move used", "Toggle reaction used"):
            self.assertEqual(commands[label][2], True)

        roster_source = inspect.getsource(GearPrinterApp._encounter_roster_context_menu)
        self.assertIn("_encounter_character_menu_entries", roster_source)

    def test_action_source_click_expands_selects_and_reveals_originating_gear(self) -> None:
        now = datetime.now().astimezone().isoformat()
        gear = EquipmentSelection("blade", "Weapons", 1, 2, "Arc Blade", "Acme", {"Rank": "5"}, 20, 20)
        source = Character(
            "hero", "Hero", "Human", "Generalist", 5, 1, "digest", 100, 20,
            {key: 0 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
            {key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
            [gear], [], [], {}, now, now,
        )
        actor = Combatant.from_character(source)
        action = next(entry for entry in actor.actions if entry.source == "Gear:blade")

        class TreeStub(self.WidgetStub):
            def __init__(self, selection=()):
                super().__init__(selection)
                self.seen = []

            def exists(self, identity):
                return identity == "blade"

            def selection_set(self, identity):
                self._selection = (identity,)

            def see(self, identity):
                self.seen.append(identity)

        class TextStub:
            def __init__(self):
                self.seen = []

            def see(self, index):
                self.seen.append(index)

        app = GearPrinterApp.__new__(GearPrinterApp)
        app.current_encounter = EncounterSession(combatants=[actor])
        app.encounter_tree = self.WidgetStub((actor.id,))
        app.encounter_gear_tree = TreeStub()
        app.encounter_stats_text = TextStub()
        app.encounter_status_var = self.VarStub()
        app.encounter_expanded_items = {}
        app._encounter_sheet_tag_names = {("gear", "blade"): "gear-tag"}
        app._refresh_encounter_inspector = lambda: None

        self.assertTrue(app._expand_encounter_action_source(action.id))
        self.assertEqual(app.encounter_expanded_items[actor.id], {"blade"})
        self.assertEqual(app.encounter_gear_tree.selection(), ("blade",))
        self.assertEqual(app.encounter_gear_tree.seen, ["blade"])
        self.assertEqual(app.encounter_stats_text.seen, ["gear-tag.first"])
        self.assertIn("source of Arc Blade", app.encounter_status_var.get())

    def test_action_source_jump_requires_shift_click(self) -> None:
        action = ActionCard(name="Strike")

        class ActionTree(self.WidgetStub):
            def identify_row(self, _y):
                return action.id

            def exists(self, identity):
                return identity == action.id

            def selection_set(self, identity):
                self._selection = (identity,)

        app = GearPrinterApp.__new__(GearPrinterApp)
        app.encounter_action_tree = ActionTree()
        app._clear_encounter_resolution_preview = lambda: None
        expanded = []
        app._expand_encounter_action_source = lambda identity: expanded.append(identity)
        event = type("Event", (), {"y": 4, "state": 0})()

        app._encounter_action_clicked(event)
        event.state = 0x0001
        app._encounter_action_clicked(event)

        self.assertEqual(expanded, [action.id])

    def test_dragged_action_logs_against_initiative_label(self) -> None:
        actor = Combatant.ad_hoc("Hero", 5)
        marker = Objective(name="End of round", kind="Marker", initiative=0)
        session = EncounterSession(combatants=[actor], objectives=[marker])
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.current_encounter = session
        app.encounter_tree = self.WidgetStub((actor.id,))
        summaries = []

        def mutate(event_type, summary, operation, **_kwargs):
            summaries.append((event_type, summary))
            operation(session)

        app._encounter_mutate = mutate
        action = actor.actions[0]
        app._encounter_use_action_on_target(action.id, marker.id)

        self.assertTrue(actor.standard_used)
        self.assertIn("logged against initiative label", summaries[0][1])

    def test_dragged_action_pins_attacker_and_resolves_combatant_target(self) -> None:
        actor = Combatant.ad_hoc("Hero", 5)
        target = Combatant.ad_hoc("Enemy", 5)
        session = EncounterSession(combatants=[actor, target])
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.current_encounter = session
        # Simulate the roster selection moving to the drop target during the
        # Treeview gesture. The drag payload must still identify the attacker.
        app.encounter_tree = self.WidgetStub((target.id,))
        events = []

        def mutate(event_type, summary, operation, **kwargs):
            operation(session)
            events.append((event_type, summary, kwargs.get("dice")))

        app._encounter_mutate = mutate
        action = actor.actions[0]
        app._encounter_use_action_on_target(action.id, target.id, actor.id)

        self.assertTrue(actor.standard_used)
        self.assertEqual(len(events), 1)
        self.assertNotIn("initiative label", events[0][1])
        self.assertIsNotNone(events[0][2]["attack"])

    def test_action_drag_suppresses_text_selection_and_tracks_ghost(self) -> None:
        action = ActionCard(name="Strike")

        class TextStub:
            def __init__(self):
                self.removed = []
                self._bindtags = ("text-widget", "Text", "root", "all")

            def tag_remove(self, tag, start, end):
                self.removed.append((tag, start, end))

            def bindtags(self, value=None):
                if value is not None:
                    self._bindtags = value
                return self._bindtags

            def winfo_class(self):
                return "Text"

        app = GearPrinterApp.__new__(GearPrinterApp)
        app.encounter_stats_text = TextStub()
        app.encounter_action_tree = self.WidgetStub()
        shown = []
        moved = []
        app._show_encounter_action_drag_ghost = lambda identity, x, y: shown.append((identity, x, y))
        app._move_encounter_action_drag_ghost = lambda x, y: moved.append((x, y))
        start = type("Event", (), {"x_root": 10, "y_root": 10, "state": 0})()
        motion = type("Event", (), {"x_root": 20, "y_root": 12})()

        self.assertEqual(app._encounter_sheet_action_drag_start(start, action.id), "break")
        self.assertEqual(app._encounter_action_drag_motion(motion), "break")
        motion.x_root = 25
        self.assertEqual(app._encounter_action_drag_motion(motion), "break")

        self.assertGreaterEqual(len(app.encounter_stats_text.removed), 2)
        self.assertEqual(shown, [(action.id, 20, 12)])
        self.assertEqual(moved, [(25, 12)])
        self.assertNotIn("Text", app.encounter_stats_text.bindtags())
        app._cancel_encounter_action_drag()
        self.assertIn("Text", app.encounter_stats_text.bindtags())

    def test_sheet_right_click_dispatches_only_the_tagged_menu(self) -> None:
        action = ActionCard(name="Strike")

        class TextStub:
            def index(self, _position):
                return "1.0"

            def tag_names(self, _index):
                return ("action-tag",)

        app = GearPrinterApp.__new__(GearPrinterApp)
        app.encounter_stats_text = TextStub()
        app._encounter_sheet_tag_targets = {"action-tag": ("action", action.id)}
        calls = []
        app._encounter_tag_context_menu = lambda _event, kind, identity: calls.append((kind, identity)) or "break"
        app._selected_encounter_combatant = lambda: (_ for _ in ()).throw(AssertionError("general menu opened"))
        event = type("Event", (), {"x": 2, "y": 3})()

        self.assertEqual(app._encounter_sheet_context_menu(event), "break")
        self.assertEqual(calls, [("action", action.id)])

    def test_manual_action_has_no_expandable_equipment_source(self) -> None:
        self.assertEqual(GearPrinterApp._encounter_action_source_item_id(ActionCard(source="Manual")), "")
        self.assertEqual(GearPrinterApp._encounter_action_source_item_id(ActionCard(source="Intrinsic:Unarmed")), "")

    def test_inline_preview_then_apply_uses_manual_dice(self) -> None:
        action = ActionCard(name="Strike", rank=10, fixed_attack=20)
        actor = Combatant(name="Hero", faction="Heroes", actions=[action])
        target = Combatant(name="Bandit", faction="Enemies")
        session = EncounterSession(combatants=[actor, target])
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.current_encounter = session
        app.encounter_tree = self.WidgetStub((actor.id,))
        app.encounter_action_tree = self.WidgetStub((action.id,))
        app.encounter_target_var = self.VarStub("Bandit — Enemies")
        app._encounter_target_ids = {"Bandit — Enemies": target.id}
        app.encounter_attack_die_var = self.VarStub("10")
        app.encounter_resistance_die_var = self.VarStub("1")
        app.encounter_resolve_status_var = self.VarStub()
        app.encounter_apply_button = self.WidgetStub()
        app.encounter_log_button = self.WidgetStub()
        app._encounter_resolution_preview = None
        mutations = []

        def mutate(event_type, summary, operation, **metadata):
            mutations.append((event_type, summary, metadata))
            operation(session)

        app._encounter_mutate = mutate
        app._encounter_preview_action()

        self.assertEqual(app._encounter_resolution_preview["attack"]["die"], 10)
        self.assertEqual(app._encounter_resolution_preview["resistance"]["die"], 1)
        self.assertEqual(target.bruises, 0)
        self.assertEqual(app.encounter_apply_button.options["state"], "normal")

        app._encounter_apply_action_preview()

        self.assertEqual(target.bruises, 1)
        self.assertEqual(mutations[0][0], "Resolution")

    def test_die_input_rejects_values_outside_d20(self) -> None:
        with self.assertRaises(ValueError):
            GearPrinterApp._encounter_die("21", "Attack die")

    def test_inline_condition_fields_create_fixed_and_save_ends_conditions(self) -> None:
        actor = Combatant(name="Hero")
        session = EncounterSession(round=3, combatants=[actor])
        captured = []
        controller = type("ControllerStub", (), {"session": session, "add_condition": lambda _self, actor_id, condition: captured.append((actor_id, condition))})()
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.current_encounter = session
        app.encounter_controller = controller
        app.encounter_tree = self.WidgetStub((actor.id,))
        app.encounter_condition_name_var = self.VarStub("Dazed")
        app.encounter_condition_duration_var = self.VarStub("fixed_rounds")
        app.encounter_condition_rounds_var = self.VarStub("2")
        app.encounter_condition_recovery_var = self.VarStub("WILL")
        app.encounter_condition_dc_var = self.VarStub("18")
        app.encounter_status_var = self.VarStub()
        app._clear_encounter_resolution_preview = lambda: None
        app._refresh_encounter_view = lambda: None

        app._encounter_add_condition()
        self.assertEqual(captured[-1][1].rounds_remaining, 2)
        self.assertEqual(captured[-1][1].start_round, 3)

        app.encounter_condition_duration_var.set("save_ends")
        app._encounter_add_condition()
        self.assertEqual(captured[-1][1].recovery_defense, "WILL")
        self.assertEqual(captured[-1][1].dc, 18)


class EncounterGenerationDefaultsTests(unittest.TestCase):
    def test_character_creator_race_chances_become_encounter_defaults(self) -> None:
        app = GearPrinterApp.__new__(GearPrinterApp)
        source = GeneratorSettings(
            race="Dwarf",
            role="Bruiser",
            race_enabled={"Human": False, "Dwarf": True},
            race_weights={"Human": 0.0, "Dwarf": 8.0},
            parent_enabled={"Human": False, "Dwarf": True},
            parent_weights={"Human": 0.0, "Dwarf": 3.0},
        )
        app._character_settings = lambda: source
        app.encounter_guarantee_weapon_var = type("BooleanVarStub", (), {"get": lambda self: True})()

        defaults = app._encounter_generator_defaults()

        self.assertEqual(defaults.race, "Random")
        self.assertEqual(defaults.role, "Random")
        self.assertTrue(defaults.guarantee_weapon)
        self.assertEqual(defaults.race_weights["Dwarf"], 8.0)
        self.assertEqual(defaults.parent_weights["Dwarf"], 3.0)


class InitiativePresetTests(unittest.TestCase):
    def test_add_recent_markers_adds_all_selected_presets_in_one_update(self) -> None:
        app = GearPrinterApp.__new__(GearPrinterApp)
        session = EncounterSession(name="Test")
        app._ensure_encounter = lambda: None
        captured = []

        def mutate(event_type, summary, operation, **kwargs):
            captured.append((event_type, summary, kwargs))
            operation(session)

        app._encounter_mutate = mutate
        app._encounter_add_recent_markers([("Players", 15), ("Lair", 10)])

        self.assertEqual([(item.name, item.initiative) for item in session.objectives], [("Players", 15), ("Lair", 10)])
        self.assertEqual(len(captured), 1)


class AddCatalogSelectionTests(unittest.TestCase):
    def test_manual_add_keeps_over_capacity_bionic(self) -> None:
        gear = parse_category_csv(
            "Bionics", 20,
            b"Manufacturer,Name,Type,Effects,Rank,Description,Special Notes,Price,Cap\n"
            b"All,Huge Implant,Implant,Feature 1,1,-,-,10,100\n",
        )[0]
        now = datetime.now().astimezone().isoformat()
        character = Character(
            "id", "", "Human", "Generalist", 5, 1, "d", 500, 0,
            {key: 0 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
            {key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
            [], [], [], {}, now, now,
        )
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.current_character = character
        app.character_generator = CharacterGenerator()
        app.snapshot = CatalogSnapshot((gear,), datetime.now().astimezone(), "digest")
        app._selected_items = lambda: [gear]
        app._character_settings = lambda: GeneratorSettings(race="Human", budget=500)
        app._recalculate_character = lambda: None
        app.char_catalog_ignore_limits_var = type("BoolStub", (), {"get": lambda self: False})()
        app.char_status_var = type("StatusStub", (), {"set": lambda self, value: setattr(self, "value", value)})()

        app._add_catalog_item()

        self.assertIn("Huge Implant", {item.name for item in character.equipment})
        self.assertIn("Added 1 selected", app.char_status_var.value)

    def test_ignore_limits_add_raises_budget_and_still_counts_capacity(self) -> None:
        gear = parse_category_csv(
            "Bionics", 21,
            b"Manufacturer,Name,Type,Effects,Rank,Description,Special Notes,Price,Cap\n"
            b"All,Free Implant,Implant,Feature 1,1,-,-,5000,100\n",
        )[0]
        now = datetime.now().astimezone().isoformat()
        character = Character(
            "id", "", "Human", "Generalist", 5, 1, "d", 0, 0,
            {key: 0 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
            {key: 0 for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
            [], [], [], {}, now, now,
        )
        app = GearPrinterApp.__new__(GearPrinterApp)
        app.current_character = character
        app.character_generator = CharacterGenerator()
        app.snapshot = CatalogSnapshot((gear,), datetime.now().astimezone(), "digest")
        app._selected_items = lambda: [gear]
        app._character_settings = lambda: GeneratorSettings(race="Human", budget=0)
        app.char_budget_var = type("BudgetStub", (), {
            "set": lambda self, value: setattr(self, "value", value),
            "get": lambda self: getattr(self, "value", 0),
        })()

        def recalculate():
            character.spent = round(sum(item.adjusted_price for item in character.equipment), 2)
            character.budget = float(app.char_budget_var.get())

        app._recalculate_character = recalculate
        app.char_catalog_ignore_limits_var = type("BoolStub", (), {"get": lambda self: True})()
        app.char_status_var = type("StatusStub", (), {"set": lambda self, value: setattr(self, "value", value)})()

        app._add_catalog_item()

        item = next(item for item in character.equipment if item.name == "Free Implant")
        self.assertEqual(item.adjusted_price, 5_000)
        self.assertEqual(character.spent, 5_000)
        self.assertEqual(character.budget, 5_000)
        self.assertEqual(character.bionic_capacity()[0], 100)
        self.assertGreater(character.cyberpsychosis_points(), 0)


if __name__ == "__main__":
    unittest.main()
