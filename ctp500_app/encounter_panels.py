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


def equipment_tooltip_text(item: EquipmentSelection) -> str:
    """Format snapshot metadata and every source-dataset field for hover help."""

    summary = (
        ("Name", item.name),
        ("Category", item.category),
        ("Manufacturer", item.manufacturer or "—"),
        ("Quantity", str(item.quantity)),
        ("Base price", f"{item.base_price:g}"),
        ("Adjusted price", f"{item.adjusted_price:g}"),
        ("Capacity", f"{item.capacity:g}"),
        ("Corruption", str(item.corruption)),
        ("Catalog source", f"sheet {item.source_gid}, row {item.source_row}"),
    )
    dataset = tuple(
        (str(label), str(value).replace("\r", " ").replace("\n", " ") or "—")
        for label, value in item.fields.items()
    )
    extras: list[tuple[str, str]] = []
    if item.modifiers:
        extras.append(("Modifiers", ", ".join(
            f"{name} {amount:+d}" for name, amount in item.modifiers.items())))
    if item.notes:
        extras.append(("Notes", "; ".join(item.notes)))
    extras.append(("Locked", "Yes" if item.locked else "No"))

    all_rows = (*summary, *dataset, *extras)
    label_width = min(22, max(len(label) for label, _value in all_rows))

    def format_rows(rows: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> list[str]:
        return [f"{label[:label_width]:<{label_width}}  {value}" for label, value in rows]

    lines = [item.name, "", "ITEM", *format_rows(summary), "", "DATASET"]
    lines.extend(format_rows(dataset) if dataset else ["No source fields available."])
    lines.extend(("", "DETAILS", *format_rows(extras)))
    return "\n".join(lines)
