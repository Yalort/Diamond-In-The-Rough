from __future__ import annotations

"""Deterministic, rules-driven physical descriptions for character sheets.

The engine deliberately consumes semantic transformation state but only emits
observable details.  Callers can therefore keep transformation flags private.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ThresholdBand:
    """Inclusive lower bound and the vocabulary used at that tier."""

    minimum: int
    label: str
    phrase: str


STAT_THRESHOLDS: dict[str, tuple[ThresholdBand, ...]] = {
    "STR": (
        ThresholdBand(-10, "frail", "a slight, underpowered build"),
        ThresholdBand(0, "ordinary", "an unexceptional amount of muscle"),
        ThresholdBand(3, "fit", "a fit, visibly conditioned build"),
        ThresholdBand(6, "powerful", "dense, powerful musculature"),
        ThresholdBand(10, "massive", "massive, clearly superhuman musculature"),
        ThresholdBand(15, "colossal", "a colossal frame built for impossible force"),
    ),
    "STA": (
        ThresholdBand(-10, "delicate", "a delicate, easily wearied constitution"),
        ThresholdBand(0, "healthy", "a healthy, mortal constitution"),
        ThresholdBand(3, "hardy", "a hardy, weathered constitution"),
        ThresholdBand(6, "unyielding", "an unyielding, injury-resistant constitution"),
        ThresholdBand(10, "invulnerable", "an almost invulnerable physical density"),
        ThresholdBand(15, "monolithic", "monolithic durability that strains belief"),
    ),
    "AGL": (
        ThresholdBand(-10, "awkward", "stiff, uncertain balance"),
        ThresholdBand(0, "steady", "steady, everyday coordination"),
        ThresholdBand(3, "graceful", "graceful, economical balance"),
        ThresholdBand(6, "acrobatic", "acrobatic control in every shift of weight"),
        ThresholdBand(10, "preternatural", "preternaturally fluid body control"),
        ThresholdBand(15, "untrackable", "motion too seamless for the eye to follow"),
    ),
    "FGT": (
        ThresholdBand(-10, "unguarded", "an open, unpracticed guard"),
        ThresholdBand(0, "ready", "the neutral readiness of basic training"),
        ThresholdBand(3, "trained", "a trained fighter's guarded posture"),
        ThresholdBand(6, "veteran", "a veteran's compact, threat-aware stance"),
        ThresholdBand(10, "masterful", "a master combatant's effortless readiness"),
        ThresholdBand(15, "perfect", "perfectly placed limbs that never seem exposed"),
    ),
    "AWE": (
        ThresholdBand(-10, "distant", "a distant, poorly focused gaze"),
        ThresholdBand(0, "attentive", "an ordinarily attentive gaze"),
        ThresholdBand(3, "alert", "alert eyes that continually take in the room"),
        ThresholdBand(6, "piercing", "a piercing, difficult-to-surprise gaze"),
        ThresholdBand(10, "all-seeing", "an uncannily all-seeing focus"),
        ThresholdBand(15, "oracular", "an oracular awareness of every nearby motion"),
    ),
    "DEX": (
        ThresholdBand(-10, "clumsy", "blunt, clumsy hand movements"),
        ThresholdBand(0, "capable", "capable, workmanlike hands"),
        ThresholdBand(3, "precise", "precise, practiced fingers"),
        ThresholdBand(6, "surgical", "surgically exact hand control"),
        ThresholdBand(10, "impossible", "impossibly exact micro-movements"),
        ThresholdBand(15, "mechanical", "precision finer than calibrated machinery"),
    ),
    "INT": (
        ThresholdBand(-10, "vacant", "a vacant, slow-to-resolve expression"),
        ThresholdBand(0, "thoughtful", "a plainly thoughtful expression"),
        ThresholdBand(3, "keen", "a keen, rapidly assessing expression"),
        ThresholdBand(6, "brilliant", "a brilliant, visibly calculating focus"),
        ThresholdBand(10, "prodigious", "a prodigious mind evident in constant appraisal"),
        ThresholdBand(15, "incomprehensible", "an incomprehensible intelligence behind the eyes"),
    ),
    "PRE": (
        ThresholdBand(-10, "withdrawn", "a withdrawn, easily overlooked bearing"),
        ThresholdBand(0, "reserved", "a reserved, ordinary social bearing"),
        ThresholdBand(3, "striking", "a striking, self-possessed bearing"),
        ThresholdBand(6, "commanding", "a commanding presence that controls attention"),
        ThresholdBand(10, "mesmerizing", "a mesmerizing, almost tangible charisma"),
        ThresholdBand(15, "mythic", "a mythic presence that dominates the surroundings"),
    ),
}


def threshold_band(stat: str, value: int) -> ThresholdBand:
    """Return the highest descriptor tier whose lower bound is met."""

    bands = STAT_THRESHOLDS[stat.upper()]
    # Values can be manually edited beyond the generator's usual range. Clamp
    # them to the nearest authored tier rather than leaking a lookup failure.
    return next((band for band in reversed(bands) if value >= band.minimum), bands[0])


def _stable_choice(seed: int, key: str, values: Sequence[str]) -> str:
    digest = hashlib.sha256(f"{seed}:{key}".encode("utf-8")).digest()
    return values[int.from_bytes(digest[:4], "big") % len(values)]


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "0", "false", "no", "off", "none"}
    return bool(value)


RACE_DETAILS: dict[str, tuple[str, ...]] = {
    "Corvid": ("a feathered avian head and a hard beak", "broad wings folded along the back", "taloned lower legs"),
    "Kangaa": ("a long-eared marsupial profile", "a balancing tail and a concealed abdominal pouch", "oversized, spring-loaded feet"),
    "Nightwalker": ("predatory features framed by pronounced canines", "light-sensitive, dark-adapted eyes", "clawed hands and a hunter's forward balance"),
    "Ong-Ban Huna": ("heavy facial architecture", "a titanically enlarged torso over a metallic skeleton", "thick, bullet-resistant skin"),
    "Wolfman": ("a bestial, long-jawed head", "dense fur over an enlarged hunting frame", "clawed hands and digitigrade legs"),
    "Xzzykmnrglhtsjt": ("a minute, wall-dwelling body", "a narrow flexible trunk", "gripping extremities suited to vertical surfaces"),
}


FLAG_DETAILS: dict[str, str] = {
    "bestial": "Animal traits reshape the silhouette: the jaw projects, body hair thickens, and the limbs settle into a predatory geometry.",
    "amorphous": "The body has no reliable hard outline; its tissues sag, gather, and reform with slow fluid motion.",
    "aquatic": "Water-adapted anatomy is visible in streamlined contours, sealed features, and subtle respiratory slits.",
    "avian": "Feathers, wing structures, and lightweight limb proportions mark an unmistakably avian anatomy.",
    "crystalline": "Faceted crystalline growths plate the skin and catch light along hard geometric edges.",
    "cybernetic": "Artificial seams, access points, and manufactured surfaces interrupt the organic anatomy.",
    "full_conversion": "Little natural tissue remains visible; the figure reads as a purpose-built mechanical chassis.",
    "gaseous": "The outline frays into drifting particulate or vapor and never becomes completely still.",
    "giant": "Every proportion is enlarged, producing a towering silhouette and unusually long reach.",
    "miniature": "The entire anatomy is compressed to a distinctly miniature scale without losing adult proportion.",
    "mutated": "Asymmetries and newly expressed tissues show that the body has been rewritten rather than naturally grown.",
    "serpentine": "Serpentine structures alter the head and lower silhouette, moving with independent muscular tension.",
    "shapeshifter": "Small details refuse to stay fixed: contours settle, features drift, and skin texture subtly reselects itself.",
    "undead": "The body shows no ordinary warmth or respiration, and its stillness resembles preserved rather than living tissue.",
}


class PhysicalDescriptionEngine:
    """Translate final stats and private state into a top-to-bottom profile."""

    @staticmethod
    def infer_flags(race: str, equipment: Sequence[object], settings: Mapping[str, object]) -> set[str]:
        flags: set[str] = set()
        race_text = race.casefold()
        traits = " ".join(str(getattr(item, "name", "")) + " " + " ".join(map(str, getattr(item, "fields", {}).values())) for item in equipment).casefold()
        rules = {
            "amorphous": ("gelosean", "amorphous", "fluid form"),
            "aquatic": ("qoaxl", "ichthi", "aquatic", "gills"),
            "avian": ("corvid", "volanans", "avian", "wings"),
            "crystalline": ("gelosean: blue", "crystal", "carapace"),
            "cybernetic": ("gamma tauist", "cyberlimb", "cyberarm", "cyberleg", "bionic"),
            "full_conversion": ("gamma tauist: extreme", "gamma tauist: forfeiter", "full conversion"),
            "gaseous": ("bakk'ur", "gaseous", "particulate"),
            "giant": ("ong-ban huna", "medusan", "growth", "giant"),
            "miniature": ("xzzy", "shrinking", "miniature"),
            "mutated": ("mutation", "genetic rewrite", "gene treatment"),
            "serpentine": ("medusan", "serpent", "snake"),
            "shapeshifter": ("wolfman", "gelosean: green", "morph", "shapeshift"),
            "undead": ("nightwalker", "undead", "deathless", "vampir"),
            "bestial": ("wolfman", "nightwalker", "claw", "fang", "bestial"),
        }
        source = f"{race_text} {traits}"
        for flag, needles in rules.items():
            if any(needle in source for needle in needles):
                flags.add(flag)
        if any(getattr(item, "category", "") == "Genemods" for item in equipment):
            flags.add("mutated")
        if any(getattr(item, "category", "") == "Bionics" for item in equipment):
            flags.add("cybernetic")

        private = settings.get("transformation_flags", settings.get("_transformation_flags", {}))
        if isinstance(private, Mapping):
            for name, enabled in private.items():
                normalized = re.sub(r"[^a-z0-9]+", "_", str(name).casefold()).strip("_")
                if normalized in FLAG_DETAILS:
                    if _truthy(enabled):
                        flags.add(normalized)
                    else:
                        flags.discard(normalized)
        elif isinstance(private, (list, tuple, set)):
            flags.update(str(value).casefold() for value in private if str(value).casefold() in FLAG_DETAILS)
        return flags

    def describe(
        self,
        *,
        race: str,
        stats: Mapping[str, int],
        seed: int = 0,
        equipment: Sequence[object] = (),
        settings: Mapping[str, object] | None = None,
    ) -> str:
        settings = settings or {}
        score = {key: int(stats.get(key, 0)) for key in STAT_THRESHOLDS}
        band = {key: threshold_band(key, value) for key, value in score.items()}
        flags = self.infer_flags(race, equipment, settings)
        complexion = _stable_choice(seed, "complexion", ("even", "weathered", "pale", "warm-toned", "deep-toned", "mottled"))
        hair = _stable_choice(seed, "hair", ("close-cropped", "short and practical", "loosely worn", "carefully kept", "unruly"))

        overall = (
            f"Overall: This {race} has {band['STR'].phrase} and {band['STA'].phrase}. "
            f"Their silhouette carries {band['PRE'].phrase}, while their resting posture shows {band['FGT'].phrase}."
        )
        head = (
            f"Head and face: {hair.capitalize()} hair frames a {complexion} complexion. "
            f"The face holds {band['INT'].phrase}; its expression gives the impression of someone {band['PRE'].label}."
        )
        eyes = (
            f"Eyes and senses: The eyes maintain {band['AWE'].phrase}. Their focus is {band['AWE'].label}, "
            f"tracking nearby movement with {band['DEX'].label} precision."
        )
        torso = (
            f"Neck and torso: The neck, shoulders, chest, and abdomen are proportioned around {band['STR'].phrase}. "
            f"The visible tissue suggests {band['STA'].phrase}."
        )
        arms = (
            f"Arms and hands: The arms match the torso's {band['STR'].label} development. "
            f"The hands move with {band['DEX'].phrase}, and settle naturally into {band['FGT'].phrase}."
        )
        legs = (
            f"Hips, legs, and feet: The lower body is balanced for {band['AGL'].phrase}. "
            f"Weight passes cleanly from hip to foot, supported by a {band['STA'].label} frame."
        )
        movement = (
            f"Movement: In motion they display {band['AGL'].phrase}; when threatened, that movement resolves into "
            f"{band['FGT'].phrase}."
        )
        paragraphs = [overall, head, eyes, torso, arms, legs, movement]

        racial = list(RACE_DETAILS.get(race, ()))
        if race.startswith("Gelosean"):
            racial.extend(("a translucent, cell-like surface", "a soft anatomy capable of redistributing its mass"))
        elif race.startswith("Medusan"):
            racial.extend(("a crown of living serpents", "a towering warrior's frame"))
        if racial:
            paragraphs.append("Ancestral anatomy: " + "; ".join(racial) + ".")
        if flags:
            paragraphs.append("Visible alterations: " + " ".join(FLAG_DETAILS[flag] for flag in sorted(flags)))
        return "\n".join(paragraphs)


def physical_description(character: object) -> str:
    """Convenience adapter for the application's Character model."""

    return PhysicalDescriptionEngine().describe(
        race=character.race,
        # Physical vocabulary represents the underlying character, not racial,
        # equipment, corruption, or other calculated stat adjustments.
        stats=character.base_abilities,
        seed=character.seed,
        equipment=character.equipment,
        settings=character.settings,
    )
