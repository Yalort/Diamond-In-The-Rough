from __future__ import annotations

import copy
import math
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable, Iterable
from uuid import uuid4

from .character_data import AMMO_ALIASES
from .characters import Character, CharacterGenerator, GeneratorSettings, ToughnessBreakdown, format_defenses, format_speed
from .models import CatalogSnapshot, PrintDocument


DIFFICULTY_MULTIPLIERS = {
    "Easy": 0.5, "Standard": 1.0, "Hard": 1.5, "Severe": 2.0, "Extreme": 3.0,
}
STANDARD_CONDITIONS = (
    "Burning", "Bleeding", "Poisoned", "Diseased", "Dying", "Dead",
    "Dazed", "Compelled", "Controlled", "Defenseless", "Disabled", "Entranced",
    "Exhausted", "Fatigued", "Hindered", "Immobile", "Impaired", "Incapacitated",
    "Paralyzed", "Prone", "Restrained", "Staggered", "Stunned", "Surprised",
    "Transformed", "Unaware", "Vulnerable", "Weakened",
)
CUSTOM_CONDITIONS = ("Burning", "Bleeding", "Poisoned", "Diseased", "Dying")
ABILITY_KEYS = ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")
DURATION_MODES = ("manual", "start_turn", "end_turn", "fixed_rounds", "save_ends", "sustained", "concentration", "scene", "permanent")

DAMAGE_TYPE_RULES: dict[str, dict[str, object]] = {
    "Slug": {"shield": "ignored", "armor": "resisted", "effect": "damage", "resistance": "TOUGH", "label": "Physical Damage"},
    "Laser": {"shield": "blocked", "armor": "resisted", "effect": "damage", "resistance": "TOUGH", "label": "Energy Damage", "note": "No ammunition required."},
    "Plasma": {"shield": "resisted", "armor": "resisted", "effect": "damage", "resistance": "TOUGH", "label": "Energy Damage", "note": "An overcharged attack may add +5 effect rank; its 10% catastrophic-failure check belongs to the absent attacker and is not applied here."},
    "Arc": {"shield": "destroyed", "armor": "arc", "effect": "affliction", "resistance": "FORT", "label": "Arc Afflictions", "degrees": ("Dazed", "Stunned", "Incapacitated"), "note": "Metal armor does not fully protect."},
    "Fire": {"shield": "resisted", "armor": "resisted", "effect": "damage", "resistance": "TOUGH", "label": "Energy Damage", "secondary": (0.20, "Ignited")},
    "Rad": {"shield": "blocked", "armor": "ignored", "effect": "affliction", "resistance": "FORT", "label": "Radiation Afflictions", "degrees": ("Fatigued", "Exhausted", "Incapacitated"), "note": "Insidious: radiation damage may remain undiagnosed."},
    "Cryo": {"shield": "resisted", "armor": "resisted", "effect": "affliction", "resistance": "FORT", "label": "Freezing Afflictions", "degrees": ("Hindered", "Immobile", "Incapacitated")},
    "Acid": {"shield": "ignored", "armor": "destroyed", "effect": "damage", "resistance": "TOUGH", "label": "Physical Damage", "continuous": "Acid Burning", "note": "Continuous until cleared."},
    "Poison": {"shield": "ignored", "armor": "poison", "effect": "affliction", "resistance": "FORT", "label": "Poison Afflictions", "degrees": ("Impaired", "Disabled", "Incapacitated"), "note": "Delivery determines penetration; sealed armor blocks, otherwise this resolver assumes bypass."},
    "Disease": {"shield": "ignored", "armor": "ignored", "effect": "affliction", "resistance": "FORT", "label": "Disease Afflictions", "degrees": ("Fatigued", "Exhausted", "Incapacitated"), "note": "Highly illegal almost everywhere."},
    "Sonic": {"shield": "destroyed", "armor": "ignored", "effect": "damage", "resistance": "TOUGH", "label": "Physical Damage", "note": "The attack also deals one-quarter damage to its absent wielder; that backlash is not applied here."},
    "Gravity": {"shield": "resisted", "armor": "ignored", "effect": "damage", "resistance": "TOUGH", "label": "Physical Damage", "secondary": (0.20, "Crippled"), "note": "Absurdly rare and immensely costly."},
    "Void": {"shield": "destroyed", "armor": "destroyed", "effect": "damage", "resistance": "TOUGH", "label": "Energy Damage", "secondary": (0.20, "Severed"), "note": "Absurdly rare and immensely costly."},
    "EMP": {"shield": "destroyed", "armor": "emp", "effect": "damage", "resistance": "TOUGH", "label": "Energy Damage only to electronics", "electronics_only": True, "note": "Disrupts communications in the area."},
    "Psychic": {"shield": "ignored", "armor": "ignored", "effect": "damage", "resistance": "WILL", "label": "Psychic Damage"},
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


@dataclass(slots=True)
class ActionCard:
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "New Action"
    source: str = "Manual"
    action_type: str = "Standard"
    effect_type: str = "Damage"
    range: str = "Close"
    attack_basis: str = "FGT"
    attack_modifier: int = 0
    fixed_attack: int | None = None
    target_defense: str = "PARRY"
    resistance_defense: str = "TOUGH"
    rank: int = 0
    dc: int | None = None
    area: bool = False
    perception: bool = False
    multiattack: int = 0
    descriptors: list[str] = field(default_factory=list)
    degree_conditions: dict[str, list[str]] = field(default_factory=dict)
    duration: str = "instant"
    recovery: str = ""
    notes: str = ""
    resource_name: str = ""
    resource_cost: int = 0
    resource_item_id: str = ""
    condition_triggers: dict[str, float] = field(default_factory=dict)
    persistent: bool = False

    @property
    def resistance_dc(self) -> int:
        if self.dc is not None:
            return self.dc
        return self.rank + (15 if self.effect_type.casefold() == "damage" else 10)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ActionCard":
        return cls(**value)  # type: ignore[arg-type]


@dataclass(slots=True)
class ConditionInstance:
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Dazed"
    source: str = ""
    rank: int = 0
    dc: int = 0
    start_round: int = 1
    duration_mode: str = "manual"
    rounds_remaining: int | None = None
    expiry_timing: str = "end_turn"
    recovery_defense: str = ""
    notes: str = ""
    active: bool = True
    suppressed: bool = False
    original_rank: int = 0
    saves_made: int = 0
    stage: int = 0
    modifier_ids: list[str] = field(default_factory=list)
    child_condition_ids: list[str] = field(default_factory=list)

    @property
    def recovery_dc(self) -> int:
        return self.dc if self.dc else 10 + self.rank

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ConditionInstance":
        return cls(**value)  # type: ignore[arg-type]


@dataclass(slots=True)
class RuntimeModifier:
    id: str = field(default_factory=lambda: str(uuid4()))
    target: str = "ALL_CHECKS"
    amount: int = 0
    source: str = ""
    notes: str = ""
    active: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RuntimeModifier":
        return cls(**value)  # type: ignore[arg-type]


@dataclass(slots=True)
class ResourceCounter:
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Hero Points"
    current: int = 0
    maximum: int | None = None
    refresh: str = "manual"
    notes: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ResourceCounter":
        return cls(**value)  # type: ignore[arg-type]


@dataclass(slots=True)
class LootEntry:
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Loot"
    source_combatant_id: str = ""
    source_name: str = ""
    gear_identity: str = ""
    quantity: int = 1
    condition: str = "Used"
    value: float = 0.0
    credits: int = 0
    revealed: bool = False
    excluded: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "LootEntry":
        return cls(**value)  # type: ignore[arg-type]


@dataclass(slots=True)
class Objective:
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Objective"
    kind: str = "Objective"
    initiative: int | None = None
    dc: int = 10
    progress: int = 0
    target: int = 4
    state: str = "Active"
    timer: int | None = None
    actions: list[ActionCard] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "Objective":
        data = dict(value)
        data["actions"] = [ActionCard.from_dict(item) for item in data.get("actions", [])]  # type: ignore[arg-type]
        return cls(**data)  # type: ignore[arg-type]


@dataclass(slots=True)
class EnvironmentModifier:
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Environment"
    zone: str = "Global"
    target: str = "ALL_CHECKS"
    amount: int = 0
    cover: int = 0
    concealment: str = ""
    active: bool = True
    notes: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "EnvironmentModifier":
        return cls(**value)  # type: ignore[arg-type]


@dataclass(slots=True)
class Combatant:
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Combatant"
    kind: str = "Character"
    faction: str = "Enemies"
    power_level: int = 5
    minion: bool = False
    visible: bool = True
    source_character_id: str = ""
    source_updated_at: str = ""
    character_snapshot: dict[str, object] | None = None
    ad_hoc_abilities: dict[str, int] = field(default_factory=dict)
    ad_hoc_defenses: dict[str, int] = field(default_factory=dict)
    initiative: int | None = None
    initiative_group: str = ""
    delayed: bool = False
    readied: bool = False
    holding: bool = False
    skipped: bool = False
    defeated: bool = False
    zone: str = "Global"
    bruises: int = 0
    conditions: list[ConditionInstance] = field(default_factory=list)
    modifiers: list[RuntimeModifier] = field(default_factory=list)
    actions: list[ActionCard] = field(default_factory=list)
    resources: list[ResourceCounter] = field(default_factory=list)
    standard_used: bool = False
    move_used: bool = False
    free_used: int = 0
    reaction_used: bool = False
    notes: str = ""

    @classmethod
    def from_character(cls, character: Character, *, faction: str = "Enemies", minion: bool = False) -> "Combatant":
        combatant = cls(
            name=character.name or character.race,
            faction=faction,
            power_level=character.power_level,
            minion=minion,
            source_character_id=character.id,
            source_updated_at=character.updated_at,
            character_snapshot=copy.deepcopy(character.to_dict()),
            resources=[ResourceCounter(name="Hero/Villain Points", current=0)],
        )
        combatant.actions = RulesEngine.derive_actions(combatant)
        return combatant

    @classmethod
    def ad_hoc(cls, name: str, power_level: int, *, faction: str = "Enemies", minion: bool = False) -> "Combatant":
        combatant = cls(
            name=name, power_level=power_level, faction=faction, minion=minion, kind="Ad Hoc",
            ad_hoc_abilities={key: 0 for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")},
            ad_hoc_defenses={key: power_level for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")},
        )
        combatant.actions = RulesEngine.derive_actions(combatant)
        return combatant

    def character(self) -> Character | None:
        return Character.from_dict(copy.deepcopy(self.character_snapshot)) if self.character_snapshot else None

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "Combatant":
        data = dict(value)
        data["conditions"] = [ConditionInstance.from_dict(item) for item in data.get("conditions", [])]  # type: ignore[arg-type]
        data["modifiers"] = [RuntimeModifier.from_dict(item) for item in data.get("modifiers", [])]  # type: ignore[arg-type]
        data["actions"] = [ActionCard.from_dict(item) for item in data.get("actions", [])]  # type: ignore[arg-type]
        data["resources"] = [ResourceCounter.from_dict(item) for item in data.get("resources", [])]  # type: ignore[arg-type]
        combatant = cls(**data)  # type: ignore[arg-type]
        manual = [action for action in combatant.actions if not RulesEngine.is_derived_action(action)]
        combatant.actions = RulesEngine.derive_actions(combatant) + manual
        return combatant


@dataclass(slots=True)
class RecipeSlot:
    id: str = field(default_factory=lambda: str(uuid4()))
    source_type: str = "Generated"
    source_character_id: str = ""
    name: str = "Generated enemy"
    quantity_min: int = 1
    quantity_max: int = 1
    settings: dict[str, object] = field(default_factory=dict)
    faction: str = "Enemies"
    minion: bool = False
    initiative_group: str = ""
    required: bool = True
    unique: bool = False
    locked: bool = False
    weight: float = 1.0
    threat_override: float | None = None

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RecipeSlot":
        return cls(**value)  # type: ignore[arg-type]


@dataclass(slots=True)
class Campaign:
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "New Campaign"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    party_character_ids: list[str] = field(default_factory=list)
    recurring_character_ids: list[str] = field(default_factory=list)
    chronology: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "Campaign":
        return cls(**value)  # type: ignore[arg-type]


@dataclass(slots=True)
class EncounterTemplate:
    id: str = field(default_factory=lambda: str(uuid4()))
    campaign_id: str = ""
    name: str = "New Encounter"
    folder: str = ""
    tags: list[str] = field(default_factory=list)
    factions: list[str] = field(default_factory=lambda: ["Heroes", "Enemies"])
    combatants: list[Combatant] = field(default_factory=list)
    recipe_slots: list[RecipeSlot] = field(default_factory=list)
    objectives: list[Objective] = field(default_factory=list)
    environment: list[EnvironmentModifier] = field(default_factory=list)
    notes: str = ""
    party_size: int = 4
    party_power_level: int = 10
    difficulty: str = "Standard"
    threat_multiplier: float = 1.0
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "EncounterTemplate":
        data = dict(value)
        data["combatants"] = [Combatant.from_dict(item) for item in data.get("combatants", [])]  # type: ignore[arg-type]
        data["recipe_slots"] = [RecipeSlot.from_dict(item) for item in data.get("recipe_slots", [])]  # type: ignore[arg-type]
        data["objectives"] = [Objective.from_dict(item) for item in data.get("objectives", [])]  # type: ignore[arg-type]
        data["environment"] = [EnvironmentModifier.from_dict(item) for item in data.get("environment", [])]  # type: ignore[arg-type]
        return cls(**data)  # type: ignore[arg-type]


@dataclass(slots=True)
class EncounterSession:
    id: str = field(default_factory=lambda: str(uuid4()))
    template_id: str = ""
    campaign_id: str = ""
    name: str = "Encounter"
    status: str = "Active"
    round: int = 1
    turn_index: int = 0
    combatants: list[Combatant] = field(default_factory=list)
    objectives: list[Objective] = field(default_factory=list)
    environment: list[EnvironmentModifier] = field(default_factory=list)
    loot: list[LootEntry] = field(default_factory=list)
    merchant_ids: list[str] = field(default_factory=list)
    active_merchant_id: str = ""
    notes: str = ""
    event_cursor: int = 0
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @classmethod
    def from_template(cls, template: EncounterTemplate) -> "EncounterSession":
        session = cls(
            template_id=template.id, campaign_id=template.campaign_id, name=template.name,
            combatants=copy.deepcopy(template.combatants), objectives=copy.deepcopy(template.objectives),
            environment=copy.deepcopy(template.environment), notes=template.notes,
        )
        stage_loot(session)
        return session

    def ordered_combatants(self) -> list[Combatant]:
        return sorted(self.combatants, key=lambda item: (item.initiative is None, -(item.initiative or -10_000), item.name.casefold()))

    def current_combatant(self) -> Combatant | None:
        ordered = self.ordered_combatants()
        return ordered[self.turn_index % len(ordered)] if ordered else None

    def ordered_turn_entities(self) -> list[Combatant | Objective]:
        """Return combatants plus active hazards/objectives with initiative."""
        entities: list[Combatant | Objective] = list(self.combatants)
        entities.extend(item for item in self.objectives if item.initiative is not None and item.state == "Active")
        return sorted(entities, key=lambda item: (item.initiative is None, -(item.initiative or -10_000), item.name.casefold()))

    def current_entity(self) -> Combatant | Objective | None:
        ordered = self.ordered_turn_entities()
        return ordered[self.turn_index % len(ordered)] if ordered else None

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "EncounterSession":
        data = dict(value)
        data["combatants"] = [Combatant.from_dict(item) for item in data.get("combatants", [])]  # type: ignore[arg-type]
        data["objectives"] = [Objective.from_dict(item) for item in data.get("objectives", [])]  # type: ignore[arg-type]
        data["environment"] = [EnvironmentModifier.from_dict(item) for item in data.get("environment", [])]  # type: ignore[arg-type]
        data["loot"] = [LootEntry.from_dict(item) for item in data.get("loot", [])]  # type: ignore[arg-type]
        return cls(**data)  # type: ignore[arg-type]


@dataclass(slots=True)
class EncounterEvent:
    id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str = ""
    sequence: int = 0
    timestamp: str = field(default_factory=now_iso)
    round: int = 1
    actor_id: str = ""
    event_type: str = "Edit"
    summary: str = ""
    before: dict[str, object] = field(default_factory=dict)
    after: dict[str, object] = field(default_factory=dict)
    dice: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "EncounterEvent":
        return cls(**value)  # type: ignore[arg-type]


class RulesEngine:
    @staticmethod
    def active_conditions(combatant: Combatant) -> set[str]:
        return {item.name.casefold() for item in combatant.conditions if item.active and not item.suppressed}

    @staticmethod
    def active_condition(combatant: Combatant, name: str) -> ConditionInstance | None:
        return next((item for item in combatant.conditions if item.active and not item.suppressed and item.name.casefold() == name.casefold()), None)

    @staticmethod
    def condition_triggers_from_notes(notes: str) -> dict[str, float]:
        """Parse ``N% chance of CONDITION`` notes, including wrapped whitespace."""
        names = {name.casefold(): name for name in ("Burning", "Bleeding", "Poisoned", "Diseased")}
        triggers: dict[str, float] = {
            names[match.group("condition").casefold()]: max(
                0.0, min(1.0, float(match.group("chance")) / 100.0)
            )
            for match in re.finditer(
                r"(?P<chance>\d+(?:\.\d+)?)\s*%\s*chance\s+of\s+"
                r"(?P<condition>Burning|Bleeding|Poisoned|Diseased)\b",
                notes,
                re.I,
            )
        }
        # Retain support for explicitly named legacy notes. The new source uses
        # the grammar above, while an unqualified name remains guaranteed.
        for name in ("Burning", "Bleeding", "Poisoned", "Diseased"):
            if name in triggers:
                continue
            match = re.search(rf"\b{re.escape(name)}\b", notes, re.I)
            if not match:
                continue
            triggers[name] = 1.0
        return triggers

    @staticmethod
    def condition_trigger_results(target: Combatant, action: ActionCard, rng: random.Random | None = None) -> dict[str, dict[str, object]]:
        rng = rng or random.SystemRandom()
        results: dict[str, dict[str, object]] = {}
        for name, chance in action.condition_triggers.items():
            existing = RulesEngine.active_condition(target, name)
            if existing and name in {"Burning", "Poisoned"}:
                results[name] = {"chance": chance, "roll": None, "applied": True, "reset": True, "duration": 6}
                continue
            roll = rng.random()
            applied = roll < chance
            results[name] = {
                "chance": chance, "roll": roll, "applied": applied, "reset": False,
                "duration": rng.randint(1, 6) if applied and name in {"Burning", "Poisoned"} else None,
            }
        return results

    @staticmethod
    def apply_condition_triggers(target: Combatant, action: ActionCard, results: dict[str, dict[str, object]], current_round: int) -> list[str]:
        applied: list[str] = []
        for name, result in results.items():
            if not result.get("applied"):
                continue
            existing = RulesEngine.active_condition(target, name)
            if existing:
                if name in {"Burning", "Poisoned"}:
                    existing.rounds_remaining = 6
                    existing.original_rank = action.rank
                    applied.append(f"{name} reset to 6")
                continue
            duration = int(result.get("duration") or 0) or None
            target.conditions.append(ConditionInstance(
                name=name, source=action.name, start_round=current_round,
                duration_mode="custom", rounds_remaining=duration,
                original_rank=action.rank, dc=10 if name in {"Bleeding", "Poisoned", "Dying"} else 0,
            ))
            applied.append(name)
        return applied

    @staticmethod
    def has_immortality(combatant: Combatant) -> bool:
        parts = [combatant.name, combatant.notes]
        character = combatant.character()
        if character:
            parts.extend((character.race, character.role, *character.race_template.traits))
            for item in character.equipment:
                parts.extend((item.name, *item.fields.values(), *item.notes))
        for action in combatant.actions:
            parts.extend((action.name, action.notes, *action.descriptors))
        return bool(re.search(r"\bImmortality\b", " ".join(parts), re.I))

    @staticmethod
    def condition_roll_requirements(condition: ConditionInstance) -> tuple[str, ...]:
        if condition.name.casefold() == "burning":
            return ("damage",)
        if condition.name.casefold() == "poisoned":
            return ("damage", "progression")
        if condition.name.casefold() in {"bleeding", "dying"}:
            return ("save",)
        return ()

    @staticmethod
    def tick_condition(combatant: Combatant, condition: ConditionInstance, current_round: int, rolls: dict[str, int] | None = None, rng: random.Random | None = None) -> dict[str, object]:
        """Resolve one custom-condition tick. Callers provide prompted d20 values."""
        if not condition.active or condition.suppressed or condition.name not in CUSTOM_CONDITIONS:
            raise ValueError("The selected condition cannot be ticked.")
        rolls = rolls or {}
        rng = rng or random.SystemRandom()
        for key in RulesEngine.condition_roll_requirements(condition):
            if key not in rolls or not 1 <= int(rolls[key]) <= 20:
                raise ValueError(f"{key.title()} d20 must be between 1 and 20.")
        result: dict[str, object] = {"condition": condition.name, "condition_id": condition.id, "rolls": dict(rolls), "effects": []}
        name = condition.name.casefold()

        if name in {"burning", "poisoned"}:
            rank = condition.original_rank // 2
            damage = ActionCard(name=f"{condition.name} tick", effect_type="Damage", rank=rank, resistance_defense="TOUGH")
            resistance = RulesEngine.resistance_result(combatant, damage, int(rolls["damage"]))
            RulesEngine.apply_resistance(combatant, damage, resistance, current_round)
            result["damage"] = resistance
            result["half_rank"] = rank
            result["effects"] = [*result["effects"], *resistance["suggestions"]]  # type: ignore[index]

        if name == "poisoned":
            _abilities, defenses, _speed = RulesEngine.stats(combatant)
            dc = 10 + condition.saves_made
            die = int(rolls["progression"])
            total = die + defenses.get("FORT", 0)
            condition.saves_made += 1
            success = total >= dc
            progression = {"die": die, "modifier": defenses.get("FORT", 0), "total": total, "dc": dc, "success": success}
            result["progression"] = progression
            if not success and condition.stage < 3:
                condition.stage += 1
                for child in combatant.conditions:
                    if child.id in condition.child_condition_ids:
                        child.active = False
                stage_name = ("Hindered", "Immobile", "Dying")[condition.stage - 1]
                child = ConditionInstance(name=stage_name, source=f"Poisoned:{condition.id}", start_round=current_round, duration_mode="custom", dc=10)
                combatant.conditions.append(child)
                condition.child_condition_ids.append(child.id)
                result["effects"] = [*result["effects"], stage_name]  # type: ignore[index]

        elif name in {"bleeding", "dying"}:
            abilities, _defenses, _speed = RulesEngine.stats(combatant)
            dc = 10 + condition.saves_made
            die = int(rolls["save"])
            total = die + abilities.get("STA", 0)
            condition.saves_made += 1
            success = total >= dc
            save = {"die": die, "modifier": abilities.get("STA", 0), "total": total, "dc": dc, "success": success}
            result["save"] = save
            if not success and name == "bleeding":
                combatant.bruises += 1
                result["effects"] = ["Bruise +1"]
            elif not success:
                condition.active = False
                dead = RulesEngine.active_condition(combatant, "Dead")
                if not dead:
                    combatant.conditions.append(ConditionInstance(name="Dead", source=condition.source or "Dying", start_round=current_round, duration_mode="permanent"))
                immortal = RulesEngine.has_immortality(combatant)
                combatant.defeated = not immortal
                result["dead"] = True
                result["immortal"] = immortal
                result["effects"] = ["Dead" + (" (Immortality)" if immortal else "")]

        elif name == "diseased":
            attribute = rng.choice(ABILITY_KEYS)
            modifier = RuntimeModifier(target=attribute, amount=-1, source=f"Diseased:{condition.id}", notes="Attribute weakened by Diseased")
            combatant.modifiers.append(modifier)
            condition.modifier_ids.append(modifier.id)
            result["attribute"] = attribute
            result["effects"] = [f"{attribute} -1"]

        if name in {"burning", "poisoned"} and condition.rounds_remaining is not None:
            condition.rounds_remaining -= 1
            result["rounds_remaining"] = condition.rounds_remaining
            if condition.rounds_remaining <= 0:
                condition.active = False
                if name == "poisoned":
                    for child in combatant.conditions:
                        if child.id in condition.child_condition_ids:
                            child.active = False
                result["expired"] = True
        return result

    @staticmethod
    def tick_hit_conditions(combatant: Combatant, current_round: int, rolls: dict[str, dict[str, int]], condition_ids: set[str] | None = None) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for condition in list(combatant.conditions):
            if condition.active and condition.name.casefold() in {"bleeding", "dying"} and (condition_ids is None or condition.id in condition_ids):
                results.append(RulesEngine.tick_condition(combatant, condition, current_round, rolls.get(condition.id, {})))
        return results

    @staticmethod
    def cure_diseased(combatant: Combatant) -> int:
        cured = 0
        for condition in combatant.conditions:
            if not condition.active or condition.name.casefold() != "diseased":
                continue
            condition.active = False
            cured += 1
            for modifier in combatant.modifiers:
                if modifier.id in condition.modifier_ids or modifier.source == f"Diseased:{condition.id}":
                    modifier.active = False
        return cured

    @staticmethod
    def apply_action_hit(
        target: Combatant,
        action: ActionCard,
        resistance: dict[str, object] | None,
        trigger_results: dict[str, dict[str, object]],
        current_round: int,
        hit_condition_rolls: dict[str, dict[str, int]] | None = None,
    ) -> dict[str, object]:
        """Apply one confirmed hit and every condition side effect exactly once."""
        if action.effect_type.casefold() == "healing":
            cured = RulesEngine.cure_diseased(target) if action.persistent else 0
            return {"healing": True, "diseased_cured": cured, "triggers": [], "hit_ticks": []}
        existing_hit_conditions = {
            condition.id for condition in target.conditions
            if condition.active and condition.name.casefold() in {"bleeding", "dying"}
        }
        if resistance is not None:
            RulesEngine.apply_resistance(target, action, resistance, current_round)
        hit_ticks = RulesEngine.tick_hit_conditions(target, current_round, hit_condition_rolls or {}, existing_hit_conditions)
        triggered = RulesEngine.apply_condition_triggers(target, action, trigger_results, current_round)
        return {"healing": False, "triggers": triggered, "hit_ticks": hit_ticks}

    @staticmethod
    def deactivate_condition(combatant: Combatant, condition: ConditionInstance) -> None:
        condition.active = False
        for child in combatant.conditions:
            if child.id in condition.child_condition_ids:
                child.active = False
        for modifier in combatant.modifiers:
            if modifier.id in condition.modifier_ids or modifier.source == f"Diseased:{condition.id}":
                modifier.active = False

    @staticmethod
    def stats(combatant: Combatant, environment: Iterable[EnvironmentModifier] = ()) -> tuple[dict[str, int], dict[str, int], int]:
        character = combatant.character()
        if character:
            abilities, defenses, speed = character.totals()
        else:
            abilities = {key: combatant.ad_hoc_abilities.get(key, 0) for key in ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")}
            defenses = {key: combatant.ad_hoc_defenses.get(key, 0) for key in ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")}
            speed = 2
        for modifier in combatant.modifiers:
            if not modifier.active:
                continue
            if modifier.target in abilities:
                abilities[modifier.target] += modifier.amount
            elif modifier.target in defenses:
                defenses[modifier.target] += modifier.amount
            elif modifier.target == "SPEED":
                speed += modifier.amount
        for modifier in environment:
            if not modifier.active or modifier.zone not in {"Global", combatant.zone}:
                continue
            if modifier.target in defenses:
                defenses[modifier.target] += modifier.amount
        defenses["TOUGH"] -= combatant.bruises
        conditions = RulesEngine.active_conditions(combatant)
        if "defenseless" in conditions:
            defenses["DODGE"] = defenses["PARRY"] = 0
        elif "vulnerable" in conditions:
            defenses["DODGE"] = math.floor(defenses["DODGE"] / 2)
            defenses["PARRY"] = math.floor(defenses["PARRY"] / 2)
        return abilities, defenses, speed

    @staticmethod
    def toughness_breakdown(combatant: Combatant, total_toughness: int) -> ToughnessBreakdown:
        character = combatant.character()
        if not character:
            return ToughnessBreakdown(total_toughness)
        armor_adjustment = sum(
            modifier.amount for modifier in combatant.modifiers
            if modifier.active and modifier.source.casefold() == "armor destroyed"
        )
        shield_adjustment = sum(
            modifier.amount for modifier in combatant.modifiers
            if modifier.active and modifier.source.casefold() == "shield destroyed"
        )
        return character.toughness_breakdown(
            total_toughness,
            armor_adjustment=armor_adjustment,
            shield_adjustment=shield_adjustment,
        )

    @staticmethod
    def check_penalty(combatant: Combatant) -> int:
        conditions = RulesEngine.active_conditions(combatant)
        ledger = sum(item.amount for item in combatant.modifiers if item.active and item.target in {"ALL_CHECKS", "CHECKS"})
        if "disabled" in conditions:
            return -5 + ledger
        if "impaired" in conditions:
            return -2 + ledger
        return ledger

    @staticmethod
    def initiative_modifier(combatant: Combatant) -> int:
        abilities, _defenses, _speed = RulesEngine.stats(combatant)
        result = abilities["AGL"]
        character = combatant.character()
        if character:
            text = " ".join(character.race_template.traits + tuple(item.fields.get("Effects", "") for item in character.equipment))
            result += 4 * sum(int(value or 1) for value in re.findall(r"IMPROVED INITIATIVE\s*(\d*)", text, re.I))
        result += sum(item.amount for item in combatant.modifiers if item.active and item.target == "INITIATIVE")
        return result + RulesEngine.check_penalty(combatant)

    @staticmethod
    def derive_actions(combatant: Combatant) -> list[ActionCard]:
        character = combatant.character()
        abilities, _defenses, _speed = RulesEngine.stats(combatant)
        close_attack = 0
        ranged_attack = 0
        if character:
            for trait in character.race_template.traits:
                for kind, amount in re.findall(r"\b(CLOSE|RANGED)\s+ATTACK\s+(\d+)", trait, re.I):
                    if kind.casefold() == "close":
                        close_attack += int(amount)
                    else:
                        ranged_attack += int(amount)

        unarmed_bonus = 0
        if character:
            for item in character.equipment:
                if item.category == "Bionics":
                    text = " ".join((item.fields.get("Effects", ""), item.fields.get("Special Notes", ""), *item.notes))
                    unarmed_bonus += sum(int(value) for value in re.findall(r"Damage\s*\+(\d+)\s+with\s+unarmed\s+strikes", text, re.I))
        actions: list[ActionCard] = [ActionCard(
            name="Unarmed", source="Intrinsic:Unarmed", effect_type="Damage", range="Close",
            attack_basis="FGT", fixed_attack=abilities.get("FGT", 0) + close_attack,
            target_defense="PARRY", resistance_defense="TOUGH",
            rank=max(0, abilities.get("STR", 0) + unarmed_bonus),
            descriptors=["Physical", "Unarmed"],
            notes=(f"Strength {abilities.get('STR', 0):+d}" + (f"; bionic strike bonus +{unarmed_bonus}" if unarmed_bonus else "")),
        )]
        if not character:
            return actions
        for item in character.equipment:
            if item.category == "Weapons":
                melee = item.fields.get("Form Factor", "").casefold() == "melee"
                rank_match = re.search(r"\d+", item.fields.get("Effective Rank", item.fields.get("Rank", "0")))
                ammo = item.fields.get("Ammo", "").strip()
                burst_match = re.search(r"\d+", item.fields.get("Burst", ""))
                resource_cost = int(burst_match.group()) if ammo not in {"", "-"} and burst_match else 0
                notes = item.fields.get("Special Notes", "")
                element = item.fields.get("Element", "").strip()
                actions.append(ActionCard(
                    name=item.name, source=f"Gear:{item.id}", effect_type="Damage",
                    range="Close" if melee else "Ranged", attack_basis="FGT" if melee else "DEX",
                    fixed_attack=character.to_hit_modifier(item), target_defense="PARRY" if melee else "DODGE",
                    resistance_defense="TOUGH", rank=int(rank_match.group()) if rank_match else 0,
                    multiattack=int((re.search(r"MULTIATTACK\s*(\d+)", item.fields.get("Special Notes", ""), re.I) or [0, 0])[1]),
                    descriptors=[element] if element else [], notes=notes, resource_name=ammo if resource_cost else "",
                    resource_cost=resource_cost,
                    condition_triggers=RulesEngine.condition_triggers_from_notes(notes),
                ))
                continue
            item_type = item.fields.get("Type", "").strip()
            consumable_text = f"{item_type} {item.name}".casefold()
            if item.category == "Inventory" and any(word in consumable_text for word in ("drug", "grenade", "poison", "explosive", "stim", "dose", "injector")):
                effects = " ".join(item.fields.get(key, "") for key in ("Effect", "Effects", "Special Notes", "Description")).strip()
                rank_match = re.search(r"\d+", item.fields.get("Rank", ""))
                rank = int(rank_match.group()) if rank_match else 0
                grenade = "grenade" in f"{item_type} {item.name}".casefold()
                affliction = bool(re.search(r"\bAffliction\b|\b(?:Dazed|Stunned|Impaired|Disabled|Incapacitated)\b", effects, re.I))
                actions.append(ActionCard(
                    name=item.name, source=f"Gear:{item.id}", action_type="Standard",
                    effect_type="Affliction" if affliction else "Damage" if grenade else "Special",
                    range="Ranged" if grenade else "Personal", attack_basis="DEX" if grenade else "FGT",
                    fixed_attack=abilities.get("DEX", 0) if grenade else None,
                    target_defense="DODGE" if grenade else "PARRY",
                    resistance_defense="FORT" if affliction else "TOUGH", rank=rank,
                    area=grenade, descriptors=[item_type or "Consumable"], notes=effects,
                    resource_name=item.name, resource_cost=1, resource_item_id=item.id,
                ))
                continue
            if item.category != "Bionics":
                continue
            effects = " ".join((item.fields.get("Effects", ""), item.fields.get("Special Notes", ""), *item.notes)).strip()
            rank_match = re.search(r"\d+", item.fields.get("Rank", ""))
            fallback_rank = int(rank_match.group()) if rank_match else 0
            ranged = bool(re.search(r"\bRanged\b", effects, re.I))
            area = bool(re.search(r"\b(?:Cone|Burst|Line|Cloud|Shapeable)\s+Area\b|\bArea\b", effects, re.I))
            perception = bool(re.search(r"\bPerception(?:-range)?\b", effects, re.I))
            multi_match = re.search(r"\bMultiattack\s*(\d*)", effects, re.I)
            multiattack = int(multi_match.group(1) or 1) if multi_match else 0
            base = abilities.get("DEX" if ranged else "FGT", 0) + (ranged_attack if ranged else close_attack)
            accurate_match = re.search(r"\bAccurate\s*(\d+)", effects, re.I)
            if accurate_match:
                base += int(accurate_match.group(1)) * 2
            damage_matches = [match for match in re.finditer(r"\b(?:Ranged\s+)?Damage\s*([+]?)\s*(\d+)", effects, re.I)
                              if "unarmed strikes" not in effects[match.start():match.end() + 30].casefold()]
            affliction_matches = list(re.finditer(r"\b(?:Ranged\s+)?Affliction(?:\s+(?:per\s+loaded\s+dart))?\s*(\d*)", effects, re.I))
            both = bool(damage_matches and affliction_matches)
            for match in damage_matches:
                rank = int(match.group(2))
                damage_context = effects[match.start():match.end() + 50]
                if match.group(1) or re.search(r"Strength[- ]based", damage_context, re.I):
                    rank += abilities.get("STR", 0)
                actions.append(ActionCard(
                    name=f"{item.name} — Damage" if both else item.name, source=f"Bionic:{item.id}",
                    effect_type="Damage", range="Ranged" if ranged else "Close",
                    attack_basis="DEX" if ranged else "FGT", fixed_attack=base,
                    target_defense="DODGE" if ranged else "PARRY", resistance_defense="TOUGH",
                    rank=max(0, rank), area=area, perception=perception, multiattack=multiattack,
                    descriptors=["Bionic"], notes=effects,
                ))
            for match in affliction_matches:
                rank = int(match.group(1)) if match.group(1) else fallback_rank
                resistance = "WILL" if re.search(r"\bWill\b", effects, re.I) else "FORT"
                condition_hits = [(match.start(), name) for name in STANDARD_CONDITIONS if (match := re.search(rf"\b{re.escape(name)}\b", effects, re.I))]
                conditions = [name for _position, name in sorted(condition_hits)]
                degrees = {str(index): [name] for index, name in enumerate(conditions[:3], 1)}
                actions.append(ActionCard(
                    name=f"{item.name} — Affliction" if both else item.name, source=f"Bionic:{item.id}",
                    effect_type="Affliction", range="Ranged" if ranged else "Close",
                    attack_basis="DEX" if ranged else "FGT", fixed_attack=base,
                    target_defense="DODGE" if ranged else "PARRY", resistance_defense=resistance,
                    rank=max(0, rank), area=area, perception=perception, multiattack=multiattack,
                    descriptors=["Bionic"], degree_conditions=degrees, notes=effects,
                ))
            if not damage_matches and not affliction_matches and re.search(
                r"\b(?:Regeneration|Healing|Leaping|Flight|Teleport|Communication|Concealment|Create|Environment)\b", effects, re.I
            ):
                power = re.search(r"\b(Regeneration|Healing|Leaping|Flight|Teleport|Communication|Concealment|Create|Environment)\s*(\d*)", effects, re.I)
                is_healing = bool(power and power.group(1).casefold() == "healing")
                actions.append(ActionCard(
                    name=item.name, source=f"Bionic:{item.id}", action_type="Free" if "free action" in effects.casefold() else "Standard",
                    effect_type="Healing" if is_healing else "Special", range="Personal", attack_basis="FGT", fixed_attack=None,
                    target_defense="PARRY", resistance_defense="TOUGH",
                    rank=int(power.group(2)) if power and power.group(2) else fallback_rank,
                    descriptors=["Bionic", power.group(1) if power else "Special"], notes=effects,
                    persistent=bool(is_healing and re.search(r"\bPersistent\b", effects, re.I)),
                ))
        return actions

    @staticmethod
    def is_derived_action(action: ActionCard) -> bool:
        return action.source.startswith(("Gear:", "Bionic:", "Intrinsic:"))

    @staticmethod
    def action_resource_status(combatant: Combatant, action: ActionCard) -> tuple[int, int]:
        """Return (available, required); a zero requirement means unlimited use."""
        if action.resource_cost <= 0 or not action.resource_name:
            return 0, 0
        character = combatant.character()
        if not character:
            return 0, action.resource_cost
        if action.resource_item_id:
            available = sum(item.quantity for item in character.equipment if item.id == action.resource_item_id)
        else:
            needle = AMMO_ALIASES.get(action.resource_name, action.resource_name).casefold()
            available = sum(
                item.quantity for item in character.equipment
                if item.category == "Inventory" and (
                    item.name.casefold() == needle
                    or needle in f"{item.name} {item.fields.get('Effect', '')}".casefold()
                )
            )
        return available, action.resource_cost

    @staticmethod
    def consume_action_resource(combatant: Combatant, action: ActionCard) -> tuple[int, int]:
        """Consume as much of an action's linked inventory as possible."""
        available, required = RulesEngine.action_resource_status(combatant, action)
        if required <= 0:
            return available, required
        character = combatant.character()
        if not character:
            return available, required
        remaining = min(available, required)
        needle = AMMO_ALIASES.get(action.resource_name, action.resource_name).casefold()
        for item in list(character.equipment):
            matches = item.id == action.resource_item_id if action.resource_item_id else (
                item.category == "Inventory" and (
                    item.name.casefold() == needle
                    or needle in f"{item.name} {item.fields.get('Effect', '')}".casefold()
                )
            )
            if not matches or remaining <= 0:
                continue
            spent = min(item.quantity, remaining)
            item.quantity -= spent
            remaining -= spent
            if item.quantity <= 0:
                character.equipment.remove(item)
        combatant.character_snapshot = character.to_dict()
        manual = [entry for entry in combatant.actions if not RulesEngine.is_derived_action(entry)]
        combatant.actions = RulesEngine.derive_actions(combatant) + manual
        return available, required

    @staticmethod
    def attack_result(attacker: Combatant, target: Combatant, action: ActionCard, die: int, circumstance: int = 0) -> dict[str, object]:
        abilities, _defenses, _speed = RulesEngine.stats(attacker)
        _ta, target_defenses, _ts = RulesEngine.stats(target)
        modifier = action.fixed_attack if action.fixed_attack is not None else abilities.get(action.attack_basis, 0) + action.attack_modifier
        modifier += sum(item.amount for item in attacker.modifiers if item.active and item.target in {"ATTACK", f"ATTACK:{action.name}"})
        modifier += RulesEngine.check_penalty(attacker) + circumstance
        defense = 10 + target_defenses.get(action.target_defense, 0)
        total = die + modifier
        return {"die": die, "modifier": modifier, "total": total, "defense": defense, "hit": action.area or action.perception or total >= defense, "critical": die == 20 and not attacker.minion}

    @staticmethod
    def headless_attack_result(target: Combatant, attack_total: int, defense_name: str, environment: Iterable[EnvironmentModifier] = ()) -> dict[str, object]:
        defense_name = defense_name.upper()
        if defense_name not in {"DODGE", "PARRY"}:
            raise ValueError("Choose DODGE or PARRY for the hit check.")
        _abilities, defenses, _speed = RulesEngine.stats(target, environment)
        modifier = defenses.get(defense_name, 0)
        defense = 10 + modifier
        return {"total": attack_total, "defense_name": defense_name, "modifier": modifier, "defense": defense, "hit": attack_total >= defense}

    @staticmethod
    def resistance_result(target: Combatant, action: ActionCard, die: int, circumstance: int = 0) -> dict[str, object]:
        _abilities, defenses, _speed = RulesEngine.stats(target)
        modifier = defenses.get(action.resistance_defense, 0) + circumstance
        total = die + modifier
        dc = action.resistance_dc
        degree = 0 if total >= dc else math.ceil((dc - total) / 5)
        if target.minion and degree:
            degree = 4 if action.effect_type.casefold() == "damage" else max(3, degree)
        suggestions: list[str] = []
        if action.effect_type.casefold() == "damage" and degree:
            suggestions.append("Bruise +1")
            if degree == 2:
                suggestions.append("Dazed")
            elif degree == 3:
                suggestions.append("Incapacitated" if "staggered" in RulesEngine.active_conditions(target) else "Staggered")
            elif degree >= 4:
                suggestions.append("Incapacitated")
        elif degree:
            suggestions.extend(action.degree_conditions.get(str(min(3, degree)), []))
        return {"die": die, "modifier": modifier, "total": total, "dc": dc, "degree": degree, "suggestions": suggestions}

    @staticmethod
    def damage_protection_profile(combatant: Combatant) -> dict[str, object]:
        """Infer chart-relevant protection traits from the frozen character snapshot."""
        character = combatant.character()
        shield_bonus = armor_bonus = 0
        shield_present = armor_present = False
        armor_text_parts: list[str] = []
        all_text_parts = [combatant.name, combatant.kind]
        if character:
            all_text_parts.extend((character.race, character.role))
            for item in character.equipment:
                item_text = " ".join((item.name, item.manufacturer, *item.fields.values(), *item.notes))
                all_text_parts.append(item_text)
                if item.category not in {"Armor", "Apparel"}:
                    continue
                if item.fields.get("Coverage", "").casefold() == "shield":
                    shield_present = True
                    shield_bonus += max(0, item.modifiers.get("TOUGH", 0))
                else:
                    armor_present = True
                    armor_bonus += max(0, item.modifiers.get("TOUGH", 0))
                    armor_text_parts.append(item_text)
        active = RulesEngine.active_conditions(combatant)
        if "shield destroyed" in active:
            shield_present = False
            shield_bonus = 0
        if "armor destroyed" in active:
            armor_present = False
            armor_bonus = 0
        armor_text = " ".join(armor_text_parts).casefold()
        all_text = " ".join(all_text_parts).casefold()
        nonconductive = any(token in armor_text for token in ("non-conductive", "nonconductive", "insulated", "ceramic", "polymer", "rubber"))
        conductive = any(token in armor_text for token in ("conductive", "metal", "steel", "iron", "copper")) and not nonconductive
        hardened = any(token in armor_text for token in ("emp hardening", "emp-hardened", "emp hardened", "electromagnetic hardening", "faraday"))
        sealed = any(token in armor_text for token in ("sealed", "airtight", "hazmat", "environment suit", "respirator"))
        electronic = bool(character and any(item.category == "Bionics" for item in character.equipment)) or any(
            token in all_text for token in ("electronic", "robot", "android", "drone", "cyber", "synthetic", "computer", "vehicle")
        )
        return {
            "shield_present": shield_present, "shield_bonus": shield_bonus,
            "armor_present": armor_present, "armor_bonus": armor_bonus,
            "nonconductive": nonconductive, "conductive": conductive,
            "hardened": hardened, "sealed": sealed, "electronic": electronic,
        }

    @staticmethod
    def incoming_damage_result(combatant: Combatant, damage_type: str, rank: int, die: int, rng: random.Random | None = None) -> dict[str, object]:
        if damage_type not in DAMAGE_TYPE_RULES:
            raise ValueError(f"Unknown damage type: {damage_type}")
        if rank < 0:
            raise ValueError("Effect rank cannot be negative.")
        if not 1 <= die <= 20:
            raise ValueError("Resistance roll must be between 1 and 20.")
        rule = DAMAGE_TYPE_RULES[damage_type]
        profile = RulesEngine.damage_protection_profile(combatant)
        shield_behavior = str(rule["shield"])
        armor_behavior = str(rule["armor"])
        notes = [str(rule.get("note", ""))] if rule.get("note") else []
        destroyed: list[str] = []
        excluded_toughness = 0
        blocked_by = ""

        if profile["shield_present"]:
            if shield_behavior == "blocked":
                blocked_by = "Shield"
            elif shield_behavior == "destroyed":
                destroyed.append("Shield")
                excluded_toughness += int(profile["shield_bonus"])
            elif shield_behavior == "ignored":
                excluded_toughness += int(profile["shield_bonus"])
        if rule.get("electronics_only") and not profile["electronic"] and not blocked_by:
            blocked_by = "No electronic target"
        if profile["armor_present"] and not blocked_by:
            resolved_armor = armor_behavior
            if armor_behavior == "arc":
                if profile["nonconductive"]:
                    resolved_armor = "blocked"
                else:
                    resolved_armor = "ignored"
                    notes.append("Armor was treated as conductive/unknown and ignored; explicitly non-conductive armor blocks Arc.")
            elif armor_behavior == "emp":
                resolved_armor = "blocked" if profile["hardened"] else "ignored"
            elif armor_behavior == "poison":
                resolved_armor = "blocked" if profile["sealed"] else "ignored"
            armor_behavior = resolved_armor
            if resolved_armor == "blocked":
                blocked_by = "Armor"
            elif resolved_armor == "destroyed":
                destroyed.append("Armor")
                excluded_toughness += int(profile["armor_bonus"])
            elif resolved_armor == "ignored":
                excluded_toughness += int(profile["armor_bonus"])

        resistance: dict[str, object] | None = None
        secondary = ""
        if not blocked_by:
            degrees = tuple(str(value) for value in rule.get("degrees", ()))
            action = ActionCard(
                name=f"Incoming {damage_type}", effect_type="Damage" if rule["effect"] == "damage" else "Affliction",
                rank=rank, resistance_defense=str(rule["resistance"]), descriptors=[damage_type],
                degree_conditions={str(index): [value] for index, value in enumerate(degrees, 1)},
            )
            circumstance = -excluded_toughness if action.resistance_defense == "TOUGH" else 0
            resistance = RulesEngine.resistance_result(combatant, action, die, circumstance=circumstance)
            secondary_rule = rule.get("secondary")
            if resistance["degree"] and secondary_rule:
                chance, condition = secondary_rule  # type: ignore[misc]
                if (rng or random.SystemRandom()).random() < float(chance):
                    secondary = str(condition)
                    resistance["suggestions"] = [*resistance["suggestions"], secondary]  # type: ignore[index]
            continuous = str(rule.get("continuous", ""))
            if resistance["degree"] and continuous:
                resistance["suggestions"] = [*resistance["suggestions"], continuous]  # type: ignore[index]

        return {
            "damage_type": damage_type, "rank": rank, "die": die, "label": rule["label"],
            "resistance_defense": rule["resistance"], "shield_behavior": shield_behavior,
            "armor_behavior": armor_behavior, "blocked_by": blocked_by, "destroyed": destroyed,
            "excluded_toughness": excluded_toughness, "resistance": resistance,
            "secondary": secondary, "notes": notes, "profile": profile,
        }

    @staticmethod
    def apply_incoming_damage(combatant: Combatant, result: dict[str, object], current_round: int) -> None:
        damage_type = str(result["damage_type"])
        profile = result["profile"]  # type: ignore[assignment]
        for layer in result["destroyed"]:  # type: ignore[assignment]
            condition_name = f"{layer} Destroyed"
            if condition_name.casefold() not in RulesEngine.active_conditions(combatant):
                combatant.conditions.append(ConditionInstance(name=condition_name, source=damage_type, start_round=current_round, duration_mode="scene"))
            bonus = int(profile[f"{str(layer).casefold()}_bonus"])
            if bonus and not any(modifier.active and modifier.source == condition_name for modifier in combatant.modifiers):
                combatant.modifiers.append(RuntimeModifier(target="TOUGH", amount=-bonus, source=condition_name, notes=f"Destroyed by {damage_type}"))
        resistance = result.get("resistance")
        if not resistance:
            return
        rule = DAMAGE_TYPE_RULES[damage_type]
        degrees = tuple(str(value) for value in rule.get("degrees", ()))
        action = ActionCard(
            name=f"Incoming {damage_type}", effect_type="Damage" if rule["effect"] == "damage" else "Affliction",
            rank=int(result["rank"]), resistance_defense=str(rule["resistance"]), descriptors=[damage_type],
            degree_conditions={str(index): [value] for index, value in enumerate(degrees, 1)},
        )
        RulesEngine.apply_resistance(combatant, action, resistance, current_round)  # type: ignore[arg-type]
        existing = RulesEngine.active_conditions(combatant)
        for condition_name in (result.get("secondary", ""), rule.get("continuous", "") if resistance["degree"] else ""):  # type: ignore[index]
            if condition_name and str(condition_name).casefold() not in existing:
                combatant.conditions.append(ConditionInstance(name=str(condition_name), source=damage_type, start_round=current_round, duration_mode="manual"))

    @staticmethod
    def apply_resistance(target: Combatant, action: ActionCard, result: dict[str, object], current_round: int) -> None:
        degree = int(result.get("degree", 0))
        if not degree:
            return
        if action.effect_type.casefold() == "damage":
            target.bruises += 1
        for name in result.get("suggestions", []):  # type: ignore[assignment]
            if name == "Bruise +1":
                continue
            duration = "end_turn" if name == "Dazed" else "manual"
            target.conditions.append(ConditionInstance(name=str(name), source=action.name, start_round=current_round, duration_mode=duration))
            if name == "Incapacitated":
                target.defeated = True

    @staticmethod
    def expire_conditions(combatant: Combatant, timing: str) -> None:
        for condition in combatant.conditions:
            if not condition.active:
                continue
            if condition.duration_mode == timing:
                condition.active = False
            elif condition.duration_mode == "fixed_rounds" and condition.expiry_timing == timing and condition.rounds_remaining is not None:
                condition.rounds_remaining -= 1
                if condition.rounds_remaining <= 0:
                    condition.active = False


def threat_score(power_level: int, party_power_level: int, *, minion: bool = False, override: float | None = None) -> float:
    if override is not None:
        return max(0.0, override)
    score = 2 ** ((power_level - party_power_level) / 2)
    return score * (0.25 if minion else 1.0)


def target_threat(party_size: int, difficulty: str, multiplier: float = 1.0) -> float:
    return max(1, party_size) * DIFFICULTY_MULTIPLIERS.get(difficulty, 1.0) * max(0.01, multiplier)


class EncounterBuilder:
    def __init__(self, character_generator: CharacterGenerator | None = None) -> None:
        self.character_generator = character_generator or CharacterGenerator()

    def quick_generate(
        self,
        snapshot: CatalogSnapshot,
        *,
        party_size: int,
        party_power_level: int,
        difficulty: str = "Standard",
        faction: str = "Enemies",
        minion_chance: float = 0.5,
        seed: int | None = None,
        base_settings: GeneratorSettings | None = None,
    ) -> list[Combatant]:
        rng = random.Random(seed)
        target = target_threat(party_size, difficulty)
        roster: list[Combatant] = []
        score = 0.0
        attempts = 0
        while score < target and attempts < 100:
            attempts += 1
            minion = rng.random() < minion_chance
            delta = rng.choice((-2, -1, 0, 0, 1))
            pl = max(1, party_power_level + delta)
            settings = copy.deepcopy(base_settings or GeneratorSettings())
            settings.power_level = pl
            settings.role = "Random"
            character = self.character_generator.generate(snapshot, settings, seed=rng.randrange(2**63))
            combatant = Combatant.from_character(character, faction=faction, minion=minion)
            combatant.source_character_id = ""
            combatant.initiative_group = f"{character.role} minions" if minion else ""
            contribution = threat_score(pl, party_power_level, minion=minion)
            if roster and score + contribution > target * 1.25:
                continue
            roster.append(combatant)
            score += contribution
        return roster

    def build_recipe(
        self,
        template: EncounterTemplate,
        snapshot: CatalogSnapshot,
        saved_characters: dict[str, Character],
        *,
        seed: int | None = None,
        base_settings: GeneratorSettings | None = None,
    ) -> list[Combatant]:
        rng = random.Random(seed)
        output: list[Combatant] = []
        for slot in template.recipe_slots:
            quantity = rng.randint(max(0, slot.quantity_min), max(slot.quantity_min, slot.quantity_max))
            for index in range(quantity):
                if slot.source_type == "Saved" and slot.source_character_id in saved_characters:
                    combatant = Combatant.from_character(saved_characters[slot.source_character_id], faction=slot.faction, minion=slot.minion)
                elif slot.source_type == "Ad Hoc":
                    pl = int(slot.settings.get("power_level", template.party_power_level))
                    combatant = Combatant.ad_hoc(slot.name, pl, faction=slot.faction, minion=slot.minion)
                else:
                    valid = {key: value for key, value in slot.settings.items() if key in GeneratorSettings.__dataclass_fields__}
                    settings = copy.deepcopy(base_settings or GeneratorSettings())
                    for key, value in valid.items():
                        setattr(settings, key, copy.deepcopy(value))
                    # An encounter-level guarantee takes precedence over a
                    # recipe slot's saved generator settings.
                    settings.guarantee_weapon = settings.guarantee_weapon or bool(
                        base_settings and base_settings.guarantee_weapon
                    )
                    character = self.character_generator.generate(snapshot, settings, name=slot.name, seed=rng.randrange(2**63))
                    combatant = Combatant.from_character(character, faction=slot.faction, minion=slot.minion)
                    combatant.source_character_id = ""
                combatant.initiative_group = slot.initiative_group
                if quantity > 1 and combatant.name == slot.name:
                    combatant.name = f"{combatant.name} {index + 1}"
                output.append(combatant)
        return output


def stage_loot(session: EncounterSession) -> None:
    existing = {(item.source_combatant_id, item.gear_identity): item for item in session.loot}
    for combatant in session.combatants:
        character = combatant.character()
        if not character:
            continue
        for gear in character.equipment:
            identity = gear.id
            staged = existing.get((combatant.id, identity))
            if staged:
                staged.quantity = gear.quantity
                continue
            amount_match = re.search(r"([\d,.]+)\s+Galactic Credits", gear.fields.get("Amount", ""), re.I)
            credits = int(float(amount_match.group(1).replace(",", ""))) if amount_match else 0
            session.loot.append(LootEntry(
                name=gear.name, source_combatant_id=combatant.id, source_name=combatant.name,
                gear_identity=identity, quantity=gear.quantity, value=gear.adjusted_price, credits=credits,
            ))


def reveal_loot_for(session: EncounterSession, combatant_id: str) -> None:
    for item in session.loot:
        if item.source_combatant_id == combatant_id:
            item.revealed = True


def initiative_document(session: EncounterSession) -> PrintDocument:
    lines = [session.name, f"Round {session.round}", ""]
    for item in session.ordered_combatants():
        state = "DEFEATED" if item.defeated else ", ".join(sorted(RulesEngine.active_conditions(item))) or "Ready"
        lines.append(f"{item.initiative if item.initiative is not None else '—':>3}  {item.name} [{item.faction}] — {state}")
    for item in sorted((entry for entry in session.objectives if entry.initiative is not None), key=lambda entry: -(entry.initiative or 0)):
        lines.append(f"{item.initiative:>3}  {item.name} [{item.kind}] — {item.state} {item.progress}/{item.target}")
    return PrintDocument(session.name, "Encounter Initiative", "\n".join(lines))


def combatant_document(session: EncounterSession, combatant: Combatant) -> PrintDocument:
    abilities, defenses, speed = RulesEngine.stats(combatant, session.environment)
    conditions = ", ".join(sorted(RulesEngine.active_conditions(combatant))) or "None"
    lines = [combatant.name, f"{combatant.faction}  PL {combatant.power_level}  {'MINION' if combatant.minion else 'HEROIC'}"]
    lines.append(" ".join(f"{key}:{value}" for key, value in abilities.items()))
    toughness = RulesEngine.toughness_breakdown(combatant, defenses["TOUGH"])
    lines.append(format_defenses(defenses, toughness) + f" Speed: {format_speed(speed)}")
    lines.extend((f"Bruises: {combatant.bruises}", f"Conditions: {conditions}", "", "Actions:"))
    lines.extend(f"- {action.name}: +{action.fixed_attack if action.fixed_attack is not None else action.attack_modifier}, DC {action.resistance_dc} {action.resistance_defense}" for action in combatant.actions)
    return PrintDocument(combatant.name, "Encounter Combatant", "\n".join(lines))


def loot_document(session: EncounterSession) -> PrintDocument:
    items = [item for item in session.loot if item.revealed and not item.excluded]
    lines = [f"{session.name} Loot", ""]
    for item in items:
        suffix = f" — {item.credits} cr" if item.credits else f" — {item.value:g} value"
        lines.append(f"{item.quantity}x {item.name} [{item.source_name}]{suffix}")
    lines.append("")
    lines.append(f"Credits: {sum(item.credits * item.quantity for item in items)}")
    lines.append(f"Gear value: {sum(item.value * item.quantity for item in items):g}")
    return PrintDocument(session.name, "Encounter Loot", "\n".join(lines))


def summary_document(session: EncounterSession) -> PrintDocument:
    defeated = [item.name for item in session.combatants if item.defeated]
    active = [item.name for item in session.combatants if not item.defeated]
    completed = [item.name for item in session.objectives if item.state == "Complete"]
    lines = [f"{session.name} Summary", f"Status: {session.status}  Rounds: {session.round}", "", f"Defeated: {', '.join(defeated) or 'None'}", f"Remaining: {', '.join(active) or 'None'}", f"Objectives: {', '.join(completed) or 'None'}"]
    return PrintDocument(session.name, "Encounter Summary", "\n".join(lines))


def action_document(combatant: Combatant, action: ActionCard) -> PrintDocument:
    attack = action.fixed_attack if action.fixed_attack is not None else action.attack_modifier
    dc = action.resistance_dc
    lines = [action.name, f"{combatant.name} - {action.source or 'Encounter action'}", f"{action.action_type} | {action.range} | {action.effect_type}", f"Attack {attack:+d} vs {action.target_defense}", f"DC {dc} {action.resistance_defense} | Rank {action.rank}"]
    flags = [name for enabled, name in ((action.area, "Area"), (action.perception, "Perception"), (bool(action.multiattack), f"Multiattack {action.multiattack}")) if enabled]
    if flags:
        lines.append("; ".join(flags))
    if action.notes:
        lines.extend(("", action.notes))
    return PrintDocument(action.name, "Encounter Action", "\n".join(lines))


def condition_document(combatant: Combatant, condition: ConditionInstance) -> PrintDocument:
    lines = [condition.name, f"Target: {combatant.name}", f"Source: {condition.source or '-'}", f"Duration: {condition.duration_mode} ({condition.expiry_timing})"]
    if condition.name in {"Burning", "Poisoned"}:
        lines.append(f"Ticks remaining: {condition.rounds_remaining or 0}; damage rank: {condition.original_rank // 2}")
    if condition.name == "Poisoned":
        stage = ("None", "Hindered", "Immobile", "Dying")[max(0, min(3, condition.stage))]
        lines.append(f"Stage: {stage}; next Fortitude DC: {10 + condition.saves_made}")
    elif condition.name in {"Bleeding", "Dying"}:
        lines.append(f"Next Stamina DC: {10 + condition.saves_made}")
    elif condition.name == "Diseased":
        lines.append(f"Accumulated attribute loss: {len(condition.modifier_ids)}")
    if condition.recovery_defense:
        lines.append(f"Recovery: {condition.recovery_defense} vs DC {condition.recovery_dc}")
    if condition.notes:
        lines.extend(("", condition.notes))
    return PrintDocument(condition.name, "Condition Reminder", "\n".join(lines))


class EncounterController:
    """Command façade. A store may be supplied for transactional autosave and undo."""

    def __init__(self, session: EncounterSession, store=None) -> None:
        self.session = session
        self.store = store

    def mutate(self, event_type: str, summary: str, operation: Callable[[EncounterSession], None], *, actor_id: str = "", dice: dict[str, object] | None = None) -> EncounterEvent:
        before = asdict(self.session)
        operation(self.session)
        self.session.updated_at = now_iso()
        after = asdict(self.session)
        event = EncounterEvent(
            session_id=self.session.id, round=self.session.round, actor_id=actor_id,
            event_type=event_type, summary=summary, before=before, after=after, dice=dice or {},
        )
        if self.store:
            event = self.store.commit_event(self.session, event)
        return event

    def roll_initiative(self, rng: random.Random | None = None) -> None:
        rng = rng or random.SystemRandom()

        def operation(session: EncounterSession) -> None:
            groups: dict[str, int] = {}
            for combatant in session.combatants:
                key = combatant.initiative_group.strip()
                if key and key in groups:
                    combatant.initiative = groups[key]
                else:
                    combatant.initiative = rng.randint(1, 20) + RulesEngine.initiative_modifier(combatant)
                    if key:
                        groups[key] = combatant.initiative
            session.turn_index = 0

        self.mutate("Initiative", "Rolled initiative", operation)

    def next_turn(self, condition_rolls: dict[str, dict[str, int]] | None = None, rng: random.Random | None = None) -> None:
        rng = rng or random.SystemRandom()
        tick_log: list[dict[str, object]] = []

        def operation(session: EncounterSession) -> None:
            ordered = session.ordered_turn_entities()
            if not ordered:
                return
            current = ordered[session.turn_index % len(ordered)]
            if isinstance(current, Combatant):
                RulesEngine.expire_conditions(current, "end_turn")
            session.turn_index += 1
            if session.turn_index >= len(ordered):
                session.turn_index = 0
                session.round += 1
                for objective in session.objectives:
                    if objective.timer is not None and objective.state == "Active":
                        objective.timer -= 1
                        if objective.timer <= 0:
                            objective.state = "Failed"
            upcoming = ordered[session.turn_index % len(ordered)]
            if isinstance(upcoming, Combatant):
                upcoming.standard_used = upcoming.move_used = upcoming.reaction_used = upcoming.skipped = False
                upcoming.free_used = 0
                RulesEngine.expire_conditions(upcoming, "start_turn")
                # Snapshot the list so a Dying stage created by Poisoned does not
                # make another save during the same start-of-turn trigger.
                for condition in list(upcoming.conditions):
                    if not condition.active or condition.suppressed or condition.name not in CUSTOM_CONDITIONS:
                        continue
                    supplied = dict((condition_rolls or {}).get(condition.id, {}))
                    for key in RulesEngine.condition_roll_requirements(condition):
                        supplied.setdefault(key, rng.randint(1, 20))
                    tick_log.append(RulesEngine.tick_condition(upcoming, condition, session.round, supplied, rng))
                if upcoming.defeated:
                    reveal_loot_for(session, upcoming.id)

        self.mutate("Turn", "Advanced turn", operation, dice={"condition_ticks": tick_log})

    def force_condition_tick(self, combatant_id: str, condition_id: str, rolls: dict[str, int] | None = None, rng: random.Random | None = None) -> EncounterEvent:
        rng = rng or random.SystemRandom()
        tick: dict[str, object] = {}

        def operation(session: EncounterSession) -> None:
            target = next(item for item in session.combatants if item.id == combatant_id)
            condition = next(item for item in target.conditions if item.id == condition_id)
            supplied = dict(rolls or {})
            for key in RulesEngine.condition_roll_requirements(condition):
                supplied.setdefault(key, rng.randint(1, 20))
            tick.update(RulesEngine.tick_condition(target, condition, session.round, supplied, rng))
            if target.defeated:
                reveal_loot_for(session, target.id)

        return self.mutate("Condition Tick", "Forced condition tick", operation, actor_id=combatant_id, dice={"condition_tick": tick})

    def add_bruise(self, combatant_id: str, amount: int = 1) -> None:
        self.mutate("Damage", f"Bruises {amount:+d}", lambda session: setattr(next(item for item in session.combatants if item.id == combatant_id), "bruises", max(0, next(item for item in session.combatants if item.id == combatant_id).bruises + amount)), actor_id=combatant_id)

    def add_condition(self, combatant_id: str, condition: ConditionInstance) -> None:
        self.mutate("Condition", f"Applied {condition.name}", lambda session: next(item for item in session.combatants if item.id == combatant_id).conditions.append(condition), actor_id=combatant_id)

    def mark_defeated(self, combatant_id: str, defeated: bool = True) -> None:
        def operation(session: EncounterSession) -> None:
            target = next(item for item in session.combatants if item.id == combatant_id)
            target.defeated = defeated
            if defeated:
                reveal_loot_for(session, combatant_id)

        self.mutate("Defeat", "Marked defeated" if defeated else "Restored combatant", operation, actor_id=combatant_id)

    def undo(self) -> bool:
        if not self.store:
            return False
        restored = self.store.undo(self.session.id)
        if restored:
            self.session = restored
            return True
        return False

    def redo(self) -> bool:
        if not self.store:
            return False
        restored = self.store.redo(self.session.id)
        if restored:
            self.session = restored
            return True
        return False
