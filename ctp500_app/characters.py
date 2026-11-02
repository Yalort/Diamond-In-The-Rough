from __future__ import annotations

import math
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Iterable
from uuid import uuid4

from .catalog import manufacturer_names
from .character_data import (
    AMMO_ALIASES,
    MANUFACTURER_ALIASES,
    MANUFACTURER_RULES,
    MANUFACTURER_SECTION_RULES,
    RACES,
    RACE_GROUPS,
    RaceTemplate,
    race_parent,
)
from .models import CatalogSnapshot, GearItem
from .physical_descriptions import physical_description


ABILITY_KEYS = ("STR", "AGL", "FGT", "AWE", "STA", "DEX", "INT", "PRE")
DEFENSE_KEYS = ("DODGE", "PARRY", "FORT", "TOUGH", "WILL")
DEFENSE_LINKS = {"DODGE": "AGL", "PARRY": "FGT", "FORT": "STA", "TOUGH": "STA", "WILL": "AWE"}
SECTIONS = ("Weapons", "Armor", "Bionics", "Genemods", "Inventory")
GAMMA_TAU_BIONIC_FORMULAS = {
    "Gamma Tauist: Tame": (3, 1),
    "Gamma Tauist: Extreme": (3, 2),
    "Gamma Tauist: Forfeiter": (5, 2),
}

# MnM distance-rank values expressed in feet. Character speed rank 2 is the
# ordinary distance rank 0 (30 feet); Speed power ranks add to that baseline.
_MNM_DISTANCE_FEET = {
    -5: .5, -4: 1, -3: 3, -2: 6, -1: 15, 0: 30, 1: 60,
    2: 120, 3: 250, 4: 500, 5: 900, 6: 1800, 7: 2640,
}

TOUGHNESS_TOKEN_PATTERN = re.compile(
    r"(?P<both>\+A\+S:[+-]?\d+)|(?P<armor>\+A:[+-]?\d+)|"
    r"(?P<shield>\+S:[+-]?\d+)|(?P<base>Tough:[+-]?\d+)"
)


@dataclass(frozen=True)
class ToughnessBreakdown:
    base: int
    armor_bonus: int = 0
    shield_bonus: int = 0

    def text(self) -> str:
        tokens = [f"Tough:{self.base}"]
        if self.armor_bonus:
            tokens.append(f"+A:{self.base + self.armor_bonus}")
        if self.shield_bonus:
            tokens.append(f"+S:{self.base + self.shield_bonus}")
        if self.armor_bonus and self.shield_bonus:
            tokens.append(f"+A+S:{self.base + self.armor_bonus + self.shield_bonus}")
        return " ".join(tokens)


def toughness_token_spans(text: str) -> list[tuple[str, int, int]]:
    """Return color roles and offsets for compact Toughness display tokens."""
    return [
        (next(name for name, value in match.groupdict().items() if value is not None), match.start(), match.end())
        for match in TOUGHNESS_TOKEN_PATTERN.finditer(text)
    ]


def format_defenses(
    defenses: dict[str, int],
    toughness: ToughnessBreakdown,
    *,
    title_case: bool = False,
) -> str:
    parts = []
    for key in DEFENSE_KEYS:
        if key == "TOUGH":
            parts.append(toughness.text())
        else:
            label = key.title() if title_case else key
            parts.append(f"{label}:{defenses[key]}")
    return " ".join(parts)


def speed_distance_feet(speed_rank: int) -> float:
    distance_rank = speed_rank - 2
    if distance_rank in _MNM_DISTANCE_FEET:
        return _MNM_DISTANCE_FEET[distance_rank]
    if distance_rank > 7:
        return 5280 * (2 ** (distance_rank - 8))
    return .5 / (2 ** (-5 - distance_rank))


def format_speed(speed_rank: int) -> str:
    feet = speed_distance_feet(speed_rank)
    distance = f"{int(feet):,}" if float(feet).is_integer() else f"{feet:g}"
    return f"{speed_rank} ({distance}')"


def _speed_power_ranks(text: str) -> int:
    return sum(int(value) for value in re.findall(r"\bSpeed\s+(\d+)\b", text, re.I))
SKILL_NAMES = (
    "Acrobatics", "Athletics", "Deception", "Insight", "Intimidation",
    "Investigation", "Perception", "Persuasion", "Sleight of Hand", "Stealth",
    "Technology", "Treatment", "Vehicles",
)


ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    "Bruiser": {"STR": 5, "STA": 4, "FGT": 3, "AGL": 1, "AWE": 1, "DEX": 1, "INT": 1, "PRE": 1},
    "Tank": {"STA": 6, "STR": 3, "AWE": 2, "FGT": 2, "AGL": 1, "DEX": 1, "INT": 1, "PRE": 1},
    "Skirmisher": {"AGL": 5, "FGT": 4, "AWE": 2, "STR": 2, "STA": 2, "DEX": 1, "INT": 1, "PRE": 1},
    "Marksman": {"DEX": 6, "AWE": 4, "AGL": 3, "FGT": 1, "STR": 1, "STA": 1, "INT": 1, "PRE": 1},
    "Specialist": {"INT": 6, "AWE": 4, "DEX": 2, "AGL": 1, "FGT": 1, "STR": 1, "STA": 1, "PRE": 1},
    "Face": {"PRE": 6, "AWE": 3, "INT": 2, "AGL": 2, "FGT": 1, "STR": 1, "STA": 1, "DEX": 1},
    "Generalist": {key: 1 for key in ABILITY_KEYS},
}

DEFENSE_WEIGHTS = {
    "Bruiser": {"TOUGH": 4, "FORT": 3, "PARRY": 2, "DODGE": 1, "WILL": 1},
    "Tank": {"TOUGH": 5, "FORT": 4, "WILL": 2, "PARRY": 1, "DODGE": 1},
    "Skirmisher": {"DODGE": 5, "PARRY": 4, "WILL": 2, "FORT": 1, "TOUGH": 1},
    "Marksman": {"DODGE": 4, "WILL": 3, "FORT": 2, "PARRY": 1, "TOUGH": 1},
    "Specialist": {"WILL": 5, "DODGE": 3, "FORT": 2, "PARRY": 1, "TOUGH": 1},
    "Face": {"WILL": 5, "DODGE": 3, "PARRY": 2, "FORT": 1, "TOUGH": 1},
    "Generalist": {key: 1 for key in DEFENSE_KEYS},
}


@dataclass(slots=True)
class GeneratorSettings:
    power_level: int = 5
    role: str = "Generalist"
    race: str = "Random"
    budget: float = 10_000.0
    quality: float = 0.0
    quantity: float = 0.0
    weapon_mode: str = "Auto"
    guarantee_weapon: bool = False
    affinity_strength: float = 1.0
    category_initial: dict[str, float] = field(default_factory=lambda: {
        "Weapons": .80, "Armor": .75, "Bionics": .70, "Genemods": .20, "Inventory": .90,
    })
    category_continue: dict[str, float] = field(default_factory=lambda: {
        "Weapons": .55, "Armor": .55, "Bionics": .55, "Genemods": .35, "Inventory": .60,
    })
    category_max: dict[str, int] = field(default_factory=lambda: {
        "Weapons": 4, "Armor": 4, "Bionics": 15, "Genemods": 6, "Inventory": 12,
    })
    race_enabled: dict[str, bool] = field(default_factory=dict)
    race_weights: dict[str, float] = field(default_factory=dict)
    parent_enabled: dict[str, bool] = field(default_factory=dict)
    parent_weights: dict[str, float] = field(default_factory=dict)
    quality_enabled: bool = True
    quantity_enabled: bool = True
    catalog_adds_ignore_limits: bool = False

    def normalized(self) -> "GeneratorSettings":
        self.power_level = max(1, min(20, int(self.power_level)))
        self.role = self.role if self.role in {"Random", *ROLE_WEIGHTS} else "Generalist"
        self.budget = max(0.0, float(self.budget))
        self.quality = max(-1.0, min(1.0, float(self.quality)))
        self.quantity = max(-1.0, min(1.0, float(self.quantity)))
        self.quality_enabled = bool(self.quality_enabled)
        self.quantity_enabled = bool(self.quantity_enabled)
        self.catalog_adds_ignore_limits = bool(self.catalog_adds_ignore_limits)
        self.guarantee_weapon = bool(self.guarantee_weapon)
        self.affinity_strength = max(0.0, min(5.0, float(self.affinity_strength)))
        return self


@dataclass(slots=True)
class EquipmentSelection:
    id: str
    category: str
    source_gid: int
    source_row: int
    name: str
    manufacturer: str
    fields: dict[str, str]
    base_price: float
    adjusted_price: float
    capacity: float = 0.0
    corruption: int = 0
    modifiers: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    locked: bool = False
    quantity: int = 1


@dataclass(slots=True)
class Character:
    id: str
    name: str
    race: str
    role: str
    power_level: int
    seed: int
    catalog_digest: str
    budget: float
    spent: float
    base_abilities: dict[str, int]
    defense_training: dict[str, int]
    equipment: list[EquipmentSelection]
    affinities: list[str]
    corruption_penalties: list[str]
    settings: dict[str, object]
    created_at: str
    updated_at: str

    @property
    def race_template(self) -> RaceTemplate:
        return next((race for race in RACES if race.name == self.race), RACES[0])

    def totals(self) -> tuple[dict[str, int], dict[str, int], int]:
        abilities = dict(self.base_abilities)
        defenses = {key: abilities[ability] + self.defense_training.get(key, 0) for key, ability in DEFENSE_LINKS.items()}
        speed = 2
        explicit_race_speed = sum(amount for key, amount in self.race_template.bonuses if key == "SPEED")
        for key, amount in self.race_template.bonuses:
            if key in abilities:
                abilities[key] += amount
            elif key in defenses:
                defenses[key] += amount
            elif key == "SPEED":
                speed += amount
        if not explicit_race_speed:
            speed += _speed_power_ranks(" ".join(self.race_template.traits))
        for item in self.equipment:
            for key, amount in item.modifiers.items():
                if key in abilities:
                    abilities[key] += amount
                elif key in defenses:
                    defenses[key] += amount
                elif key == "SPEED":
                    speed += amount
            speed += _speed_power_ranks(" ".join((
                item.fields.get("Effect", ""), item.fields.get("Effects", ""),
                item.fields.get("Special Notes", ""), *item.notes,
            )))
        for key in self.corruption_penalties:
            abilities[key] -= 1
        # Linked defenses track final ability changes, not only the initial base.
        for defense, ability in DEFENSE_LINKS.items():
            base_link = self.base_abilities[ability]
            defenses[defense] += abilities[ability] - base_link
        used, capacity = self._bionic_capacity_for_stamina(abilities["STA"])
        excess_points = max(0, math.ceil((used - capacity) - 1e-9))
        if excess_points:
            penalty = excess_points * 5
            for defense in defenses:
                defenses[defense] -= penalty
        return abilities, defenses, speed

    @staticmethod
    def _equipment_is_shield(item: EquipmentSelection) -> bool:
        if item.fields.get("Coverage", "").strip().casefold() == "shield":
            return True
        descriptive_text = " ".join((item.name, *item.fields.values(), *item.notes))
        return bool(re.search(r"\bshield(?:s|ed|ing)?\b", descriptive_text, re.I))

    def protection_bonuses(self) -> tuple[int, int]:
        """Return positive equipment Toughness assigned to armor and shields."""
        armor_bonus = shield_bonus = 0
        for item in self.equipment:
            bonus = max(0, item.modifiers.get("TOUGH", 0))
            if not bonus:
                continue
            if self._equipment_is_shield(item):
                shield_bonus += bonus
            else:
                armor_bonus += bonus
        return armor_bonus, shield_bonus

    def toughness_breakdown(
        self,
        total_toughness: int | None = None,
        *,
        armor_adjustment: int = 0,
        shield_adjustment: int = 0,
    ) -> ToughnessBreakdown:
        armor_bonus, shield_bonus = self.protection_bonuses()
        armor_bonus = max(0, armor_bonus + armor_adjustment)
        shield_bonus = max(0, shield_bonus + shield_adjustment)
        if total_toughness is None:
            total_toughness = self.totals()[1]["TOUGH"]
        return ToughnessBreakdown(
            base=total_toughness - armor_bonus - shield_bonus,
            armor_bonus=armor_bonus,
            shield_bonus=shield_bonus,
        )

    @property
    def corruption(self) -> int:
        total = 0
        for item in self.equipment:
            if item.category != "Genemods":
                continue
            total = 0 if item.corruption <= -10_000 else max(0, total + item.corruption)
        return total

    def recalculate_corruption_penalties(self) -> None:
        needed = self.corruption // 10
        self.corruption_penalties = self.corruption_penalties[:needed]
        if len(self.corruption_penalties) < needed:
            stable = random.Random(f"{self.seed}:{','.join(item.id for item in self.equipment if item.category == 'Genemods')}")
            generated = [stable.choice(ABILITY_KEYS) for _ in range(needed)]
            self.corruption_penalties.extend(generated[len(self.corruption_penalties):])

    def _bionic_capacity_for_stamina(self, stamina: int) -> tuple[float, float]:
        additive = 0.0
        multiplier = 1.0
        used = 0.0
        capacity_effects = [*self.race_template.traits]
        for item in self.equipment:
            if item.category != "Bionics":
                continue
            used += item.capacity
            capacity_effects.append(item.fields.get("Effects", ""))
        for effects in capacity_effects:
            additive += sum(float(value) for value in re.findall(
                r"bionic\s+capacity\s*\+\s*(\d+(?:\.\d+)?)", effects, re.I
            ))
            multiplier *= 2 ** len(re.findall(r"bionic\s+capacity\s*[x×]\s*2\b", effects, re.I))
        return used, max(0.0, stamina * 3 + additive) * multiplier

    def bionic_capacity(self) -> tuple[float, float]:
        abilities, _defenses, _speed = self.totals()
        return self._bionic_capacity_for_stamina(abilities["STA"])

    def cyberpsychosis_points(self) -> int:
        used, capacity = self.bionic_capacity()
        return max(0, math.ceil((used - capacity) - 1e-9))

    @staticmethod
    def _is_neuralware_coordinator(item: EquipmentSelection) -> bool:
        name = item.name.casefold()
        notes = item.fields.get("Special Notes", "").casefold()
        return (
            "neural processor" in name
            or name == "neuralware"
            or "prerequisite for any bionic listed as requires neuralware" in notes
        )

    def to_hit_modifier(self, weapon: EquipmentSelection) -> int:
        """Return the final attack-check modifier for a generated weapon."""
        abilities, _defenses, _speed = self.totals()
        melee = weapon.fields.get("Form Factor", "").casefold() == "melee"
        total = abilities["FGT" if melee else "DEX"]

        attack_kind = "close" if melee else "ranged"
        for trait in self.race_template.traits:
            for kind, amount in re.findall(r"\b(CLOSE|RANGED)\s+ATTACK\s+(\d+)", trait, re.I):
                if kind.casefold() == attack_kind:
                    total += int(amount)

        # Manufacturer rules store non-ability attack adjustments as notes on
        # the selected weapon (for example, "Attack +1").
        for note in weapon.notes:
            match = re.search(r"\battack\s*([+-]\d+)\b", note, re.I)
            if match:
                total += int(match.group(1))

        weapon_is_smart = "smartgun link" in weapon.fields.get("Special Notes", "").casefold()
        has_coordinator = any(self._is_neuralware_coordinator(item) for item in self.equipment)
        has_smartgun_link = any(item.name.casefold() == "smartgun link" for item in self.equipment)
        if weapon_is_smart and has_coordinator and has_smartgun_link:
            for item in self.equipment:
                effect = item.fields.get("Effects", "")
                if "smartgun" not in effect.casefold() and item.name.casefold() != "smartgun link":
                    continue
                match = re.search(r"([+-]\d+)\s+to\s+attack\s+checks", effect, re.I)
                if match:
                    total += int(match.group(1))
        return total

    def skill_chip_bonuses(self) -> dict[str, int]:
        bonuses: dict[str, int] = {}
        for item in self.equipment:
            skill = item.fields.get("Selected Skill", "").strip()
            if not skill:
                continue
            effect = item.fields.get("Effects", "")
            match = re.search(r"ENHANCED\s+SKILL\s*\(\s*([+-]?\d+)", effect, re.I)
            if match:
                bonuses[skill] = bonuses.get(skill, 0) + int(match.group(1))
        return bonuses

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "Character":
        data = dict(value)
        equipment = []
        for raw_item in data.get("equipment", []):  # type: ignore[union-attr]
            item = dict(raw_item)
            item.pop("limit_exempt", None)  # Migrate snapshots from the earlier exemption behavior.
            equipment.append(EquipmentSelection(**item))
        data["equipment"] = equipment
        return cls(**data)  # type: ignore[arg-type]


def parse_price(value: str) -> float | None:
    cleaned = value.strip().replace(",", "").replace("$", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        return float(match.group(1))
    # Ammunition rows commonly encode pack size ("1/100rnds") or a total plus
    # per-round rate ("400 @ 2/rnd"). The first number is the purchasable pack
    # price used by the source sheet.
    pack = re.match(r"(\d+(?:\.\d+)?)\s*(?:/|@)", cleaned)
    return float(pack.group(1)) if pack else None


def canonical_manufacturer(name: str) -> str:
    stripped = name.strip()
    return MANUFACTURER_ALIASES.get(stripped.casefold(), stripped)


def _weighted_choice(rng: random.Random, values: list, weights: list[float]):
    if not values:
        raise ValueError("No eligible choices")
    return rng.choices(values, weights=[max(0.000001, weight) for weight in weights], k=1)[0]


def _allocate(rng: random.Random, total: int, keys: Iterable[str], weights: dict[str, float]) -> dict[str, int]:
    result = {key: 0 for key in keys}
    key_list = list(result)
    for _ in range(total):
        result[_weighted_choice(rng, key_list, [weights.get(key, 1) for key in key_list])] += 1
    return result


def _roll_dice(rng: random.Random, expression: str) -> int:
    match = re.fullmatch(r"\s*(\d*)[dD](\d+)\s*", expression)
    if not match:
        try:
            return int(float(expression))
        except ValueError:
            return 0
    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    return sum(rng.randint(1, sides) for _ in range(count))


MODIFIER_NAMES = {
    "STRENGTH": "STR", "AGILITY": "AGL", "FIGHTING": "FGT", "AWARENESS": "AWE",
    "STAMINA": "STA", "DEXTERITY": "DEX", "INTELLECT": "INT", "PRESENCE": "PRE",
    "DODGE": "DODGE", "PARRY": "PARRY", "FORTITUDE": "FORT", "TOUGHNESS": "TOUGH",
    "PROTECTION": "TOUGH", "WILL": "WILL", "SPEED": "SPEED",
}


def parse_modifiers(text: str, *, armor: bool = False) -> dict[str, int]:
    bonuses: dict[str, int] = {}
    upper = text.upper()
    for label, amount in re.findall(r"ENHANCED\s+([A-Z]+)\s*\(?([+-]?\d+)\)?", upper):
        key = MODIFIER_NAMES.get(label)
        if key:
            bonuses[key] = bonuses.get(key, 0) + int(amount)
    for amount, label in re.findall(r"([+-]\d+)\s+(STRENGTH|AGILITY|FIGHTING|AWARENESS|STAMINA|DEXTERITY|INTELLECT|PRESENCE|DODGE|PARRY|FORTITUDE|TOUGHNESS|WILL|SPEED)\b", upper):
        key = MODIFIER_NAMES[label]
        bonuses[key] = bonuses.get(key, 0) + int(amount)
    if armor:
        matches = re.findall(r"(?:SUSTAINED\s+)?PROTECTION\s+(\d+)", upper)
        if matches:
            bonuses["TOUGH"] = bonuses.get("TOUGH", 0) + max(map(int, matches))
    return bonuses


ESSENTIAL_EXCLUDED_FIELDS = frozenset({"price", "cap", "capacity", "description", "adjusted price", "effective rank"})


def essential_item_text(item: EquipmentSelection) -> str:
    source_category = {
        "Weapons": "Weapons", "Armor": "Apparel", "Bionics": "Bionics",
        "Genemods": "Gene Treatments", "Inventory": "Consumables/Gear",
    }[item.category]
    lines = [item.name, source_category, f"Manufacturer: {item.manufacturer}"]
    if item.quantity != 1:
        lines.append(f"Quantity: {item.quantity}")
    for label, value in item.fields.items():
        if label.casefold() in {"manufacturer", *ESSENTIAL_EXCLUDED_FIELDS}:
            continue
        if value.strip().casefold() not in {"", "-", "none"}:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def character_text(
    character: Character,
    *,
    audit: bool = False,
    expanded_item_ids: set[str] | None = None,
    essential_item_ids: set[str] | None = None,
    physical_profile_expanded: bool = False,
    show_physical_profile_toggle: bool = False,
    include_equipment: bool = True,
) -> str:
    abilities, defenses, speed = character.totals()
    title = f'Name: "{character.name}"' if character.name else "Name: —"
    cyberpsychosis = character.cyberpsychosis_points()
    warning = f"  WARNING: CyberPsychosis {cyberpsychosis} (-{cyberpsychosis * 5} all defenses)" if cyberpsychosis else ""
    lines = [f"{title}, Race: \"{character.race}\"{warning}", f"Role: {character.role}  PL: {character.power_level}"]
    if audit:
        race_bonuses = {key: 0 for key in (*ABILITY_KEYS, *DEFENSE_KEYS, "SPEED")}
        gear_bonuses = dict(race_bonuses)
        corruption_bonuses = {key: 0 for key in ABILITY_KEYS}
        for key, amount in character.race_template.bonuses:
            race_bonuses[key] = race_bonuses.get(key, 0) + amount
        for item in character.equipment:
            for key, amount in item.modifiers.items():
                gear_bonuses[key] = gear_bonuses.get(key, 0) + amount
        for key in character.corruption_penalties:
            corruption_bonuses[key] -= 1

        def adjustment_line(label: str, values: dict[str, int]) -> str:
            present = " ".join(f"{key}:{amount:+d}" for key, amount in values.items() if amount)
            return f"{label}: {present or 'none'}"

        lines.append("Base: " + " ".join(f"{key}:{character.base_abilities[key]}" for key in ABILITY_KEYS))
        lines.append(adjustment_line("Race bonuses", race_bonuses))
        lines.append(adjustment_line("Equipment bonuses", gear_bonuses))
        lines.append(adjustment_line("Corruption penalties", corruption_bonuses))
        if cyberpsychosis:
            lines.append(f"CyberPsychosis: {cyberpsychosis} excess capacity point(s), all defenses:{-cyberpsychosis * 5:+d}")
        lines.append("Defense training: " + " ".join(f"{key}:+{character.defense_training.get(key, 0)}" for key in DEFENSE_KEYS))
    lines.append(" ".join(f"{key}:{abilities[key]}" for key in ABILITY_KEYS))
    lines.append(format_defenses(defenses, character.toughness_breakdown(), title_case=True) + f" Speed: {format_speed(speed)}")
    skill_bonuses = character.skill_chip_bonuses()
    if skill_bonuses:
        lines.append("Skill chips: " + " ".join(f"{skill}:{amount:+d}" for skill, amount in sorted(skill_bonuses.items())))
    lines.extend(("", "Racial Traits:"))
    lines.extend(f"- {trait}" for trait in character.race_template.traits)
    for section in SECTIONS if include_equipment else ():
        lines.extend(("", f"{section}:"))
        items = [item for item in character.equipment if item.category == section]
        if not items:
            lines.append("- None")
        for item in items:
            details = f" [{item.manufacturer}]"
            is_essential = bool(essential_item_ids and item.id in essential_item_ids)
            if item.notes and item.category != "Inventory" and not is_essential:
                details += f" — {'; '.join(item.notes)}"
            attack = f"{character.to_hit_modifier(item):+d} " if item.category == "Weapons" else ""
            quantity = f" ×{item.quantity}" if item.quantity != 1 else ""
            lines.append(f"- {attack}{item.name}{quantity}{details}")
            if expanded_item_ids and item.id in expanded_item_ids:
                for label, value in item.fields.items():
                    if label == "Manufacturer" or value.strip().casefold() in {"", "-", "none"}:
                        continue
                    if is_essential and label.casefold() in ESSENTIAL_EXCLUDED_FIELDS:
                        continue
                    lines.append(f"    {label}: {value}")
                if not is_essential:
                    lines.append(f"    Adjusted Price: {item.adjusted_price:g}")
                if item.capacity and not is_essential:
                    lines.append(f"    Capacity: {item.capacity:g}")
                if item.category == "Genemods":
                    lines.append(f"    Rolled Corruption: {item.corruption}")
    used, capacity = character.bionic_capacity()
    lines.extend(("", f"Budget: {character.spent:g}/{character.budget:g} cr", f"Bionic Capacity: {used:g}/{capacity:g}", f"Corruption: {character.corruption}"))
    if show_physical_profile_toggle or physical_profile_expanded:
        lines.extend(("", f"Physical Profile: {'[Hide]' if physical_profile_expanded else '[Show]'}" if show_physical_profile_toggle else "Physical Profile:"))
        if physical_profile_expanded:
            lines.append(physical_description(character))
    return "\n".join(lines)


class CharacterGenerator:
    def __init__(self, races: tuple[RaceTemplate, ...] = RACES) -> None:
        self.races = races

    def enabled_races(self, settings: GeneratorSettings) -> list[RaceTemplate]:
        return [
            race for race in self.races
            if settings.parent_enabled.get(race_parent(race.name), True)
            and settings.parent_weights.get(race_parent(race.name), 1.0) > 0
            and settings.race_enabled.get(race.name, True)
            and settings.race_weights.get(race.name, race.default_weight) > 0
        ]

    def _choose_race(self, rng: random.Random, settings: GeneratorSettings) -> RaceTemplate:
        enabled = self.enabled_races(settings)
        if not enabled:
            raise ValueError("Enable at least one race before generating.")
        if settings.race != "Random":
            race = next((race for race in enabled if race.name == settings.race), None)
            if race is None:
                raise ValueError("The selected race is excluded.")
            return race
        enabled_names = {race.name for race in enabled}
        groups = {
            parent: [race for race in races if race.name in enabled_names]
            for parent, races in RACE_GROUPS.items()
            if any(race.name in enabled_names for race in races)
        }
        parents = list(groups)
        parent = _weighted_choice(rng, parents, [settings.parent_weights.get(name, 1.0) for name in parents])
        subraces = groups[parent]
        return _weighted_choice(rng, subraces, [settings.race_weights.get(race.name, race.default_weight) for race in subraces])

    def generate(self, snapshot: CatalogSnapshot, settings: GeneratorSettings, *, name: str = "", seed: int | None = None) -> Character:
        settings.normalized()
        seed = seed if seed is not None else random.SystemRandom().randrange(2**63)
        rng = random.Random(seed)
        role = rng.choice(tuple(ROLE_WEIGHTS)) if settings.role == "Random" else settings.role
        race = self._choose_race(rng, settings)
        base = _allocate(rng, settings.power_level * 4, ABILITY_KEYS, ROLE_WEIGHTS[role])
        training = _allocate(rng, settings.power_level * 2, DEFENSE_KEYS, DEFENSE_WEIGHTS[role])
        manufacturers = sorted({canonical_manufacturer(m) for item in snapshot.items for m in manufacturer_names(item.manufacturer) if m.casefold() != "all"})
        affinities = rng.sample(manufacturers, k=min(2, len(manufacturers)))
        now = datetime.now().astimezone().isoformat()
        character = Character(str(uuid4()), name.strip(), race.name, role, settings.power_level, seed, snapshot.digest, settings.budget, 0.0, base, training, [], affinities, [], asdict(settings), now, now)
        categories = list(SECTIONS)
        rng.shuffle(categories)
        for category in categories:
            self._generate_section(character, snapshot.items, category, settings, rng)
        if settings.guarantee_weapon and not any(item.category == "Weapons" for item in character.equipment):
            self._guarantee_weapon(character, snapshot.items, settings, rng)
        self._add_gamma_tau_bionics(character, snapshot.items, settings, rng)
        self._add_ammo(character, snapshot.items, rng, settings)
        self._recalculate_corruption(character, rng)
        character.spent = round(sum(item.adjusted_price for item in character.equipment), 2)
        self._add_credit_chip(character, rng)
        return character

    def _guarantee_weapon(
        self,
        character: Character,
        items: tuple[GearItem, ...],
        settings: GeneratorSettings,
        rng: random.Random,
    ) -> None:
        """Add an affordable weapon, or a tightly capped budget overflow fallback."""
        candidates = [item for item in self._eligible(character, items, "Weapons") if parse_price(item.price) is not None]
        if not candidates:
            raise ValueError("Cannot guarantee a weapon because the catalog has no eligible weapons.")
        rng.shuffle(candidates)
        for item in candidates:
            selection = self._selection(rng, item, character, settings, items)
            if selection is not None:
                character.equipment.append(selection)
                character.spent += selection.adjusted_price
                return
        maximum_overflow_weapon_price = character.budget / 8
        for item in candidates:
            selection = self._selection(rng, item, character, settings, items, ignore_limits=True)
            if selection is not None and selection.adjusted_price <= maximum_overflow_weapon_price:
                character.equipment.append(selection)
                character.spent += selection.adjusted_price
                return
        raise ValueError("Cannot guarantee a weapon within the remaining budget or one-eighth of the character budget.")

    @staticmethod
    def _add_credit_chip(character: Character, rng: random.Random) -> None:
        remaining = max(0.0, character.budget - character.spent)
        percentage = rng.randint(0, 100)
        credits = round(remaining * percentage / 100)
        character.equipment.append(EquipmentSelection(
            id=str(uuid4()),
            category="Inventory",
            source_gid=-1,
            source_row=-1,
            name=f"Galactic Credit Chip ({credits:g} Galactic Credits)",
            manufacturer="Bearer",
            fields={
                "Type": "Currency",
                "Amount": f"{credits:g} Galactic Credits",
                "Special Notes": f"Lootable; rolled {percentage}% of {remaining:g} remaining credits",
            },
            base_price=0.0,
            adjusted_price=0.0,
        ))

    def _item_category(self, item: GearItem) -> str | None:
        return {"Weapons": "Weapons", "Apparel": "Armor", "Bionics": "Bionics", "Gene Treatments": "Genemods", "Consumables/Gear": "Inventory"}.get(item.category)

    def _maker_weight(self, maker: str, character: Character, settings: GeneratorSettings) -> float:
        return 1.0 + (settings.affinity_strength if maker in character.affinities else 0.0)

    @staticmethod
    def _category_manufacturers(item: GearItem, catalog_items: Iterable[GearItem]) -> list[str]:
        return sorted({
            canonical_manufacturer(name)
            for candidate in catalog_items
            if candidate.category == item.category
            for name in manufacturer_names(candidate.manufacturer)
            if name.casefold() != "all"
        })

    def _possible_makers(self, item: GearItem, catalog_items: Iterable[GearItem] = ()) -> list[str]:
        listed = [canonical_manufacturer(name) for name in manufacturer_names(item.manufacturer)]
        if any(name.casefold() == "all" for name in listed):
            category_pool = self._category_manufacturers(item, catalog_items)
            if category_pool:
                return category_pool
        return [name for name in listed if name.casefold() != "all"] or ["All"]

    def _choose_maker(self, rng: random.Random, item: GearItem, character: Character, settings: GeneratorSettings, catalog_items: Iterable[GearItem] = ()) -> str:
        makers = self._possible_makers(item, catalog_items)
        section = self._item_category(item) or item.category
        eligible = [maker for maker in makers if not self._rule_for(maker, section).get("required_race") or self._rule_for(maker, section).get("required_race") == character.race]
        if eligible:
            makers = eligible
        return _weighted_choice(rng, makers, [self._maker_weight(maker, character, settings) for maker in makers])

    def _adjust_price(self, price: float, rule: dict[str, object]) -> float:
        if "fixed_price" in rule:
            return float(rule["fixed_price"])
        price *= float(rule.get("price_multiplier", 1.0))
        if "price_cap" in rule:
            price = min(price, float(rule["price_cap"]))
        return round(price, 2)

    @staticmethod
    def _rule_for(maker: str, section: str) -> dict[str, object]:
        if section == "Inventory":
            return {}
        if (maker, section) in MANUFACTURER_SECTION_RULES:
            return MANUFACTURER_SECTION_RULES[(maker, section)]
        rule = MANUFACTURER_RULES.get(maker, {})
        groups = {
            "Weapons": {"Ardent Dynamics", "Caliber Collective", "Tarkov Systems", "Eidolon Arms", "Kythera Forge", "Brightcorp", "Sable Exchange"},
            "Armor": {"Pan-Odyne Defense", "VoxShield Technologies", "AegisCor Resonants", "Kasparov Industries", "Silkline Apparel", "Black Talon Outfitters", "RadioJack", "Glint Systems", "Sable Exchange"},
            "Bionics": {"Vyral Systems", "Hexatek Neural", "Auron Yumitech", "Ascendigo Bionics", "IronHalo Cybernetics", "Liminal Incorporated", "Johnston-Tanaka Technologies", "RadioJack", "Glint Systems", "Sable Exchange", "Calyx Medica", "Vestra Vitae"},
            "Genemods": {"HelixGene Industries", "Aeon Spire Labs", "Vellum Bioworks", "Paragon Genome", "Vestra Vitae", "Sable Exchange"},
            "Inventory": {"Yoshitake Foods", "Granrealé Foods", "Luther Trading", "Phage", "Auburn Biovats", "OmniMart", "RadioJack", "Glint Systems", "Sable Exchange", "NeuraPharm", "Loxon Therapeutics", "Calyx Medica", "Vestra Vitae", "Obsidian Automata", "MechaDyne Solutions", "SilicoCore", "Apex Robotics", "Brightcorp"},
        }
        return rule if maker in groups.get(section, set()) else {}

    def _selection(
        self,
        rng: random.Random,
        item: GearItem,
        character: Character,
        settings: GeneratorSettings,
        catalog_items: Iterable[GearItem] = (),
        *,
        ignore_limits: bool = False,
        free: bool = False,
    ) -> EquipmentSelection | None:
        base_price = parse_price(item.price)
        if base_price is None:
            return None
        maker = self._choose_maker(rng, item, character, settings, catalog_items)
        section = self._item_category(item) or item.category
        rule = self._rule_for(maker, section)
        required_race = rule.get("required_race")
        if required_race and character.race != required_race and not ignore_limits:
            return None
        adjusted = self._adjust_price(base_price, rule)
        reserve = sum(dict(character.settings.get("_ammo_reserve", {})).values()) if isinstance(character.settings.get("_ammo_reserve"), dict) else 0
        if not ignore_limits and not free and character.spent + adjusted + reserve > character.budget:
            return None
        fields = dict(item.fields)
        selection_name = item.name
        if self._is_skill_chip(item):
            skill = rng.choice(SKILL_NAMES)
            amount_match = re.search(r"ENHANCED\s+SKILL\s*\(\s*([+-]?\d+)", fields.get("Effects", ""), re.I)
            amount = int(amount_match.group(1)) if amount_match else int(fields.get("Rank", "1") or 1)
            fields["Selected Skill"] = skill
            fields["Effects"] = f"Enhanced Skill ({amount:+d} to {skill})"
            selection_name = f"{item.name} ({skill})"
        modifiers = parse_modifiers(" ".join((fields.get("Effects", ""), fields.get("Effect", ""), fields.get("Special Notes", ""))), armor=section in {"Armor", "Bionics"})
        nonstat_notes: list[str] = []
        for key, value in dict(rule.get("bonuses", {})).items():  # type: ignore[arg-type]
            if key in {*ABILITY_KEYS, *DEFENSE_KEYS, "SPEED"}:
                modifiers[key] = modifiers.get(key, 0) + int(value)
            else:
                nonstat_notes.append(f"{key.title()} {int(value):+d}")
        cap = float(fields.get("Cap", "0") or 0) if section == "Bionics" else 0.0
        cap *= float(rule.get("capacity_multiplier", 1.0))
        notes = nonstat_notes + ([str(rule["note"])] if rule.get("note") else [])
        if free and section == "Bionics":
            adjusted = 0.0
            cap = 0.0
        rank_match = re.fullmatch(r"\d+", fields.get("Rank", ""))
        if rank_match and (rule.get("effect_rank_delta") or rule.get("effect_rank_multiplier")):
            effective_rank = (int(rank_match.group()) + int(rule.get("effect_rank_delta", 0))) * int(rule.get("effect_rank_multiplier", 1))
            fields["Effective Rank"] = str(effective_rank)
            notes.append(f"Effective rank: {effective_rank}")
        if rule.get("effect_rank_multiplier"):
            multiplier = int(rule["effect_rank_multiplier"])
            modifiers = {key: value * multiplier for key, value in modifiers.items()}
        corruption = _roll_dice(rng, fields.get("Corruption", "0")) if section == "Genemods" else 0
        effect = fields.get("Effects", "")
        reduction = re.search(r"reduce corruption (?:by\s+)?(\d*[dD]\d+|to 0)", effect, re.I)
        if reduction:
            corruption = -10_000 if reduction.group(1).casefold() == "to 0" else -_roll_dice(rng, reduction.group(1))
        return EquipmentSelection(
            str(uuid4()), section, item.source_gid, item.source_row, selection_name,
            maker, fields, base_price, adjusted, cap, corruption, modifiers, notes,
        )

    @staticmethod
    def _is_neuralware_candidate(item: GearItem) -> bool:
        if item.category != "Bionics":
            return False
        name = item.name.casefold()
        notes = item.field_map.get("Special Notes", "").casefold()
        return (
            "neural processor" in name
            or name == "neuralware"
            or "prerequisite for any bionic listed as requires neuralware" in notes
        )

    @staticmethod
    def _requires_neuralware(item: GearItem) -> bool:
        return "requires neuralware" in item.field_map.get("Special Notes", "").casefold()

    @staticmethod
    def _requires_chipslot(item: GearItem) -> bool:
        return "chipslot socket" in item.field_map.get("Special Notes", "").casefold()

    @staticmethod
    def _is_skill_chip(item: GearItem) -> bool:
        return (
            item.category == "Bionics"
            and (
                "skill chip" in item.name.casefold()
                or "enhanced skill" in item.field_map.get("Effects", "").casefold()
            )
        )

    def _prerequisites_met(
        self,
        item: GearItem,
        character: Character,
        *,
        allow_forced_neuralware: bool = False,
        allow_forced_chipslot: bool = False,
    ) -> bool:
        notes = item.field_map.get("Special Notes", "").strip()
        if not notes.casefold().startswith("requires "):
            return True
        requirement = re.split(r"[.;]", notes[9:])[0].casefold()
        if any(word in requirement for word in ("maintenance", "replacement", "operate", "survive")):
            return True
        installed_items = [f"{selected.name} {selected.fields.get('Type', '')} {selected.fields.get('Coverage', '')}".casefold() for selected in character.equipment]
        installed = " | ".join(installed_items)
        recognized = False
        or_groups = {
            "voice modulator or synthetic throat": ("voice modulator", "synthetic throat"),
            "powered suit or frame hardpoint": ("powered suit", "lifting frame", "linear frame", "exoskeleton"),
        }
        for phrase, alternatives in or_groups.items():
            if phrase in requirement:
                recognized = True
                if not any(term in installed for term in alternatives):
                    return False
                requirement = requirement.replace(phrase, "")
        aliases = {
            "neuralware": ("neuralware", "neural processor"),
            "chipslot socket": ("chipslot socket",),
            "smartgun link": ("smartgun link",),
            "cyberaudio suite": ("cyberaudio suite",),
            "cyberaudio": ("cyberaudio",),
            "cyberoptic": ("cyberoptic",),
            "cyberhand": ("cyberhand",),
            "cyberlimb": ("cyberlimb", "cyberarm", "cyberleg"),
            "tentacle arm": ("tentacle arm",),
            "weapon mount": ("weapon mount",),
            "face plate": ("face plate",),
            "poison reservoir": ("poison reservoir",),
            "hand blade": ("hand blade",),
            "muscle and bone lace": ("muscle and bone lace",),
            "interface plugs": ("interface plugs",),
            "visual display": ("visual display", "subdermal viewscreen"),
            "sensory suite": ("cyberoptic", "cyberaudio", "sensor", "sensory"),
            "sensory bionics": ("cyberoptic", "cyberaudio", "sensor", "sensory"),
            "filter valves": ("filter valve",),
            "sealed optics": ("sealed optic",),
            "oxygen reserve": ("oxygen reserve",),
        }
        for key, terms in aliases.items():
            if key in requirement:
                recognized = True
                if not any(term in installed for term in terms):
                    if key == "neuralware" and allow_forced_neuralware:
                        continue
                    if key == "chipslot socket" and allow_forced_chipslot:
                        continue
                    return False
        if "two cyberlegs" in requirement:
            recognized = True
            if sum("cyberleg" in value for value in installed_items) < 2:
                return False
        # Unknown installation prose is kept out of procedural rolls until it is
        # mapped, rather than silently granting an impossible dependent item.
        return recognized

    def _force_neuralware(
        self,
        character: Character,
        items: tuple[GearItem, ...],
        settings: GeneratorSettings,
        rng: random.Random,
        *,
        ignore_limits: bool = False,
        free: bool = False,
    ) -> EquipmentSelection | None:
        if any(Character._is_neuralware_coordinator(item) for item in character.equipment):
            return None
        candidates = [item for item in items if self._is_neuralware_candidate(item)]
        rng.shuffle(candidates)
        for candidate in candidates:
            selection = self._selection(rng, candidate, character, settings, items, ignore_limits=ignore_limits, free=free)
            if selection is None:
                continue
            character.equipment.append(selection)
            character.spent += selection.adjusted_price
            return selection
        return None

    def _force_chipslot(
        self,
        character: Character,
        items: tuple[GearItem, ...],
        settings: GeneratorSettings,
        rng: random.Random,
        *,
        ignore_limits: bool = False,
        free: bool = False,
    ) -> EquipmentSelection | None:
        if any("chipslot socket" in item.name.casefold() for item in character.equipment):
            return None
        candidates = [item for item in items if item.category == "Bionics" and "chipslot socket" in item.name.casefold()]
        rng.shuffle(candidates)
        for candidate in candidates:
            if not self._prerequisites_met(candidate, character):
                continue
            selection = self._selection(rng, candidate, character, settings, items, ignore_limits=ignore_limits, free=free)
            if selection is None:
                continue
            character.equipment.append(selection)
            character.spent += selection.adjusted_price
            return selection
        return None

    @staticmethod
    def _rollback_forced_items(character: Character, forced_items: list[EquipmentSelection]) -> None:
        for forced in reversed(forced_items):
            if forced in character.equipment:
                character.equipment.remove(forced)
                character.spent -= forced.adjusted_price

    def _force_item_prerequisites(
        self,
        character: Character,
        item: GearItem,
        items: tuple[GearItem, ...],
        settings: GeneratorSettings,
        rng: random.Random,
        *,
        ignore_limits: bool = False,
        free: bool = False,
    ) -> list[EquipmentSelection] | None:
        forced_items: list[EquipmentSelection] = []
        if self._requires_neuralware(item):
            already_coordinated = any(Character._is_neuralware_coordinator(selected) for selected in character.equipment)
            forced_neuralware = self._force_neuralware(character, items, settings, rng, ignore_limits=ignore_limits, free=free)
            if forced_neuralware:
                forced_items.append(forced_neuralware)
            if not already_coordinated and forced_neuralware is None:
                return None
        if self._requires_chipslot(item):
            already_has_chipslot = any("chipslot socket" in selected.name.casefold() for selected in character.equipment)
            forced_chipslot = self._force_chipslot(character, items, settings, rng, ignore_limits=ignore_limits, free=free)
            if forced_chipslot:
                forced_items.append(forced_chipslot)
            if not already_has_chipslot and forced_chipslot is None:
                self._rollback_forced_items(character, forced_items)
                return None
        if not self._prerequisites_met(item, character):
            self._rollback_forced_items(character, forced_items)
            return None
        return forced_items

    def _eligible(self, character: Character, items: tuple[GearItem, ...], category: str) -> list[GearItem]:
        selected_ids = {(item.source_gid, item.source_row) for item in character.equipment}
        result = []
        for item in items:
            if self._item_category(item) != category or (item.source_gid, item.source_row) in selected_ids:
                continue
            if category == "Inventory" and item.field_map.get("Type", "").casefold() == "ammo":
                continue
            if category in {"Bionics", "Armor"} and not self._prerequisites_met(
                item,
                character,
                allow_forced_neuralware=category == "Bionics" and self._requires_neuralware(item),
                allow_forced_chipslot=category == "Bionics" and self._requires_chipslot(item),
            ):
                continue
            result.append(item)
        return result

    def _add_gamma_tau_bionics(
        self,
        character: Character,
        items: tuple[GearItem, ...],
        settings: GeneratorSettings,
        rng: random.Random,
    ) -> None:
        """Add the race's over-modded implant allotment without price or cap."""
        formula = GAMMA_TAU_BIONIC_FORMULAS.get(character.race)
        if not formula:
            return
        flat, multiplier = formula
        normal_total = sum(item.category == "Bionics" for item in character.equipment)
        desired_total = (normal_total + flat) * multiplier
        target = desired_total - normal_total
        added = 0
        while added < target:
            candidates = self._eligible(character, items, "Bionics")
            rng.shuffle(candidates)
            selected = False
            for item in candidates:
                forced = self._force_item_prerequisites(
                    character, item, items, settings, rng, free=True
                )
                if forced is None:
                    continue
                selection = self._selection(
                    rng, item, character, settings, items, free=True
                )
                if selection is None or added + len(forced) + 1 > target:
                    self._rollback_forced_items(character, forced)
                    continue
                character.equipment.append(selection)
                character.spent += selection.adjusted_price
                added += len(forced) + 1
                selected = True
                break
            if not selected:
                break

    def _generate_section(self, character: Character, items: tuple[GearItem, ...], category: str, settings: GeneratorSettings, rng: random.Random) -> None:
        if category == "Weapons" and settings.weapon_mode == "None":
            return
        initial = settings.category_initial.get(category, 0)
        if rng.random() >= initial:
            return
        maximum = max(0, settings.category_max.get(category, 0))
        armor_profile = rng.choice(("unarmored", "clothing", "light", "coverage")) if category == "Armor" else ""
        if armor_profile == "unarmored":
            maximum = 0
        ranked_armor = 0
        covered: set[str] = set()
        for index in range(maximum):
            candidates = self._eligible(character, items, category)
            if category == "Armor":
                if armor_profile == "clothing":
                    candidates = [x for x in candidates if x.rank in {"", "-"} and x.field_map.get("Coverage") != "Shield"]
                else:
                    candidates = [x for x in candidates if x.field_map.get("Coverage") != "Shield" and (x.rank in {"", "-"} or ranked_armor < 2)]
                    if armor_profile == "coverage" and covered:
                        candidates = sorted(candidates, key=lambda x: len(self._coverage(x) - covered), reverse=True)
            if not candidates:
                break
            weights = []
            for item in candidates:
                price = parse_price(item.price) or 0
                quality_weight = math.exp(settings.quality * math.log1p(price) / 3) if settings.quality_enabled else 1.0
                makers = self._possible_makers(item, items)
                affinity = max((self._maker_weight(m, character, settings) for m in makers), default=1)
                if category == "Weapons":
                    melee = item.kind == "Melee"
                    mode = settings.weapon_mode
                    if mode == "Auto":
                        abilities, _d, _s = character.totals()
                        mode = "Melee" if abilities["STR"] + abilities["FGT"] > abilities["DEX"] * 2 else "Ranged"
                    preference = 2.5 if (mode == "Melee") == melee else .35 if mode in {"Melee", "Ranged"} else 1
                else:
                    preference = 1
                weights.append(quality_weight * affinity * preference)
            item = _weighted_choice(rng, candidates, weights)
            forced_items = self._force_item_prerequisites(character, item, items, settings, rng) if category == "Bionics" else []
            if forced_items is None:
                continue
            selection = self._selection(rng, item, character, settings, items)
            if selection is None:
                # Remove an unaffordable/ineligible attempt without looping forever.
                self._rollback_forced_items(character, forced_items)
                continue
            if category == "Inventory":
                requested = self._generated_inventory_quantity(selection, rng)
                if requested > 1:
                    affordable = requested
                    if selection.adjusted_price > 0:
                        reserves = character.settings.get("_ammo_reserve", {})
                        reserved = sum(dict(reserves).values()) if isinstance(reserves, dict) else 0
                        affordable = max(1, int((character.budget - character.spent - reserved) // selection.adjusted_price))
                    selection.quantity = min(requested, affordable)
                    selection.adjusted_price = round(selection.adjusted_price * selection.quantity, 2)
            if category == "Bionics":
                character.equipment.append(selection)
            else:
                character.equipment.append(selection)
            character.spent += selection.adjusted_price
            if category == "Weapons":
                ammo = selection.fields.get("Ammo", "")
                reserves = character.settings.setdefault("_ammo_reserve", {})
                if isinstance(reserves, dict) and ammo not in {"", "-"} and ammo not in reserves:
                    ammo_cost = self._ammo_cost(ammo, items)
                    if ammo_cost is not None and character.spent + sum(reserves.values()) + ammo_cost <= character.budget:
                        reserves[ammo] = ammo_cost
                    elif ammo_cost is not None:
                        character.equipment.pop()
                        character.spent -= selection.adjusted_price
                        continue
            if category == "Armor":
                if item.rank not in {"", "-"}:
                    ranked_armor += 1
                covered |= self._coverage(item)
            quantity_adjustment = settings.quantity * .20 if settings.quantity_enabled else 0.0
            continuation = settings.category_continue.get(category, 0) + quantity_adjustment
            if index + 1 < maximum and rng.random() >= max(0, min(.98, continuation)):
                break
        if category == "Armor":
            _abilities, defenses, _speed = character.totals()
            shield_chance = max(.15, min(.70, .65 - defenses["TOUGH"] * .025))
            if rng.random() < shield_chance:
                shields = [item for item in items if item.category == "Apparel" and item.field_map.get("Coverage") == "Shield"]
                shields = [item for item in shields if self._prerequisites_met(item, character)]
                if shields:
                    selection = self._selection(rng, rng.choice(shields), character, settings, items)
                    if selection:
                        character.equipment.append(selection)
                        character.spent += selection.adjusted_price

    @staticmethod
    def _generated_inventory_quantity(selection: EquipmentSelection, rng: random.Random) -> int:
        text = f"{selection.fields.get('Type', '')} {selection.name}".casefold()
        if "grenade" in text:
            return rng.randint(1, 2)
        if "drug" in text:
            return rng.randint(1, 3)
        return 1

    @staticmethod
    def _coverage(item: GearItem) -> set[str]:
        value = item.field_map.get("Coverage", "")
        if value == "Full":
            return {"Head", "Chest", "Back", "Arms", "Hands", "Legs", "Feet"}
        return {part.strip() for part in value.split(",") if part.strip() not in {"", "-", "Shield"}}

    def _add_ammo(self, character: Character, items: tuple[GearItem, ...], rng: random.Random, settings: GeneratorSettings) -> None:
        ammo_names = {item.fields.get("Ammo", "") for item in character.equipment if item.category == "Weapons"}
        inventory = [item for item in items if item.category == "Consumables/Gear" and item.field_map.get("Type") == "AMMO"]
        for ammo in sorted(ammo_names - {"", "-"}):
            reserves = character.settings.get("_ammo_reserve", {})
            if isinstance(reserves, dict):
                reserves.pop(ammo, None)
            target = AMMO_ALIASES.get(ammo, ammo).casefold()
            candidates = [item for item in inventory if item.name.casefold() == target or target in (item.name + " " + item.field_map.get("Effect", "")).casefold()]
            if candidates:
                selection = self._selection(rng, rng.choice(candidates), character, settings, items)
                if selection:
                    clips = []
                    for weapon in character.equipment:
                        if weapon.category != "Weapons" or weapon.fields.get("Ammo", "") != ammo:
                            continue
                        clip_match = re.search(r"\d+", weapon.fields.get("Clip", ""))
                        if clip_match:
                            clips.append(int(clip_match.group()))
                    selection.quantity = max(clips, default=1)
                    character.equipment.append(selection)
                    character.spent += selection.adjusted_price
        character.settings.pop("_ammo_reserve", None)

    @staticmethod
    def _ammo_cost(ammo: str, items: tuple[GearItem, ...]) -> float | None:
        target = AMMO_ALIASES.get(ammo, ammo).casefold()
        values = [parse_price(item.price) for item in items if item.category == "Consumables/Gear" and item.field_map.get("Type") == "AMMO" and (item.name.casefold() == target or target in (item.name + " " + item.field_map.get("Effect", "")).casefold())]
        numeric = [value for value in values if value is not None]
        return min(numeric) if numeric else None

    @staticmethod
    def _recalculate_corruption(character: Character, rng: random.Random) -> None:
        total = 0
        for item in character.equipment:
            if item.category == "Genemods":
                total = 0 if item.corruption <= -10_000 else max(0, total + item.corruption)
        needed = total // 10
        character.corruption_penalties = [rng.choice(ABILITY_KEYS) for _ in range(needed)]
