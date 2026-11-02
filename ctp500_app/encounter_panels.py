"""Read-only projections for the encounter workspace's four content panels.

Equipment and actions are not copied or modified here; the caller resolves
actions and inventory changes through the encounter controller.
"""
from __future__ import annotations

from dataclasses import dataclass

from .characters import EquipmentSelection
from .encounters import ActionCard, Combatant


@dataclass(frozen=True)
class EncounterPanelContent:
    actions: tuple[ActionCard, ...]
    bionics: tuple[EquipmentSelection, ...]
    inventory: tuple[EquipmentSelection, ...]
    racial_traits: tuple[str, ...]
    special_actions: tuple[ActionCard, ...]


def encounter_panel_content(combatant: Combatant | None) -> EncounterPanelContent:
    """Build non-overlapping equipment categories for the currently selected actor.

    The Weapons / Actions pane deliberately lists *all* executable actions,
    including bionic and special actions, so attack selection has one consistent
    source. Bionics and Powers expose where those actions originate.
    """
    if combatant is None:
        return EncounterPanelContent((), (), (), (), ())
    character = combatant.character()
    equipment = character.equipment if character else ()
    return EncounterPanelContent(
        actions=tuple(combatant.actions),
        bionics=tuple(item for item in equipment if item.category == "Bionics"),
        inventory=tuple(item for item in equipment
                        if item.category not in {"Weapons", "Bionics"}),
        racial_traits=tuple(character.race_template.traits) if character else (),
        special_actions=tuple(action for action in combatant.actions
                              if action.source == "Manual"
                              or (action.source.startswith("Intrinsic:")
                                  and action.source != "Intrinsic:Unarmed")),
    )


def feature_label(trait: str) -> tuple[str, str]:
    """Extract a compact title and supporting effect text from a race trait."""
    title, separator, detail = trait.partition("(")
    if separator and title.strip():
        return title.strip(), "(" + detail
    return trait.strip(), ""
