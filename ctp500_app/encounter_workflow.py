"""Encounter-facing readouts and edit operations for weapon ammunition.

This module operates on the existing character equipment inventory.  A single
ammunition type may supply multiple weapons, but each weapon action displays
the type and cost belonging to that weapon.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .character_data import AMMO_ALIASES
from .characters import EquipmentSelection
from .encounters import ActionCard, Combatant, RulesEngine


@dataclass(frozen=True, slots=True)
class WeaponAmmo:
    name: str
    available: int
    per_attack: int
    tracked: bool

    @property
    def empty(self) -> bool:
        return self.tracked and self.available < self.per_attack

    @property
    def summary(self) -> str:
        if self.tracked:
            return f"{self.name}  ·  {self.available} available  ·  {self.per_attack}/attack"
        return f"{self.name}  ·  {self.available} available  ·  use untracked"


def _match_ammo(item: EquipmentSelection, name: str) -> bool:
    needle = AMMO_ALIASES.get(name, name).casefold()
    return item.category == "Inventory" and (
        item.name.casefold() == needle
        or needle in f"{item.name} {item.fields.get('Effect', '')}".casefold()
    )


def weapon_ammo(combatant: Combatant, action: ActionCard) -> WeaponAmmo | None:
    """Return a per-weapon readout, including a type with no tracked burst cost."""
    if not action.source.startswith("Gear:"):
        return None
    character = combatant.character()
    if character is None:
        return None
    gear_id = action.source.partition(":")[2]
    weapon = next((item for item in character.equipment
                   if item.id == gear_id and item.category == "Weapons"), None)
    if weapon is None:
        return None
    name = weapon.fields.get("Ammo", "").strip()
    if not name or name.casefold() in {"-", "none", "n/a"}:
        return None
    available = sum(max(0, item.quantity) for item in character.equipment if _match_ammo(item, name))
    required = max(0, action.resource_cost)
    return WeaponAmmo(name, available, required, required > 0)


def change_weapon_ammo(combatant: Combatant, action: ActionCard, delta: int) -> int:
    """Adjust the compatible inventory pool, then rederive weapon actions.

    Adding the first round creates a zero-cost, clearly marked manual inventory
    counter. This is a session bookkeeping adjustment, not a catalog purchase.
    Changes are persisted by the caller through EncounterController.mutate().
    """
    info = weapon_ammo(combatant, action)
    if info is None:
        raise ValueError("This is not an ammunition-using weapon action.")
    character = combatant.character()
    if character is None:
        raise ValueError("Combatant has no equipment inventory.")
    candidates = [item for item in character.equipment if _match_ammo(item, info.name)]
    if delta > 0:
        if candidates:
            candidates[0].quantity += delta
        else:
            canonical_name = AMMO_ALIASES.get(info.name, info.name)
            character.equipment.append(EquipmentSelection(
                id=str(uuid4()), category="Inventory", source_gid=0, source_row=0,
                name=canonical_name, manufacturer="Manual", fields={
                    "Name": canonical_name, "Type": "Ammunition", "Effect": canonical_name,
                }, base_price=0.0, adjusted_price=0.0, quantity=delta,
                notes=["Manual encounter ammunition adjustment"],
            ))
    elif delta < 0:
        remaining = -delta
        for item in candidates:
            taken = min(item.quantity, remaining)
            item.quantity -= taken
            remaining -= taken
            if remaining <= 0:
                break
        depleted_ids = {item.id for item in candidates if item.quantity <= 0}
        character.equipment = [item for item in character.equipment if item.id not in depleted_ids]
    else:
        return info.available
    combatant.character_snapshot = character.to_dict()
    manual_actions = [entry for entry in combatant.actions if not RulesEngine.is_derived_action(entry)]
    combatant.actions = RulesEngine.derive_actions(combatant) + manual_actions
    return max(0, info.available + delta)
