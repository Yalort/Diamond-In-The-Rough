from __future__ import annotations

"""Editable built-in data for the minion generator.

Traits are deliberately kept as source text.  ``bonuses`` contains only effects
that are unconditional enough to participate in automatic recalculation.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RaceTemplate:
    name: str
    traits: tuple[str, ...]
    bonuses: tuple[tuple[str, int], ...] = ()
    default_weight: float = 1.0


COMMON_GELOSEAN = (
    "Amorphous Cell (Insubstantial 1 (Fluid), Morph 1; Immunity 5: Critical Hits, Disease, Poison)",
    "Protein Hunter (Regeneration 2, Source: Consumed Biomass)",
)
COMMON_MEDUSAN = (
    "Towering Warrior (Growth 2, Permanent, Innate)",
    "Serpent Crown (Radius Vision, Danger Sense)",
    "Clan Energy Streams (Damage 7, Ranged, Multiattack 2)",
    "Petrifying Gaze (Affliction 9, Limited to Skilled Elders)",
    "Serpentine Maw (Feature 2; Damage 2 Bite)",
)


def _race(name: str, *traits: str, bonuses=()) -> RaceTemplate:
    return RaceTemplate(name, tuple(traits), tuple(bonuses))


RACES: tuple[RaceTemplate, ...] = (
    _race("Corvid", "Avian Physiology (Flight 4; Extended Vision 2)", "Merchant's Eye (Detect Value, Acute, Analytical)", "Silver Tongue (Connected, Well-Informed, Contacts)"),
    _race("Deep Spacer: Vanor", "Void Adapted (Immunity 10: Life Support)", "Magnetic Grip (Wall-Crawling 2, Ferrous Surfaces Only)", "Mineral Subsistence (Immunity 2; Regeneration 1, Sunlight)"),
    _race("Deep Spacer: Fon Fon", "Bonded Devotion (Acute Tracking, Bonded Partner Only)", "Perfect Companion (Enhanced Presence 2; Attractive 2)", "Feline Grace (Enhanced Agility 1; Safe Fall; Low-Light Vision; Acute Hearing)", "Widow's Madness (Enhanced Fighting 4, conditional and uncontrolled)", bonuses=(("PRE", 2), ("AGL", 1))),
    _race("Deep Spacer: Ghilnacht", "Living Computer (Quickness 8, Mental Tasks; Enhanced Intellect 4)", "Perfect Recall (Eidetic Memory; Speed of Thought)", "Data Interface (Comprehend Machines 2)", bonuses=(("INT", 4),)),
    _race("Deep Spacer: Srodun", "Hyper-Survivor (Immunity 20)", "Unkillable Frame (Protection 6, Impervious; Regeneration 5)", "Stellar Tolerance (Immunity 10: Heat and Radiation)", bonuses=(("TOUGH", 6),)),
    _race("Deep Spacer: Renpatar", "Peak Perfection (Enhanced Strength 4, Enhanced Agility 4)", "Effortless Feats (Leaping 2, Speed 2, Power-Lifting 2)", "Warrior's Longevity (Immunity 1: Aging, Half Effect)", bonuses=(("STR", 4), ("AGL", 4), ("SPEED", 2))),
    _race("Deep Spacer: Qoaxl", "Amphibious (Swimming 4; Immunity 3)", "Regenerative Limbs (Regeneration 4, Persistent)", "Leviathan-World Survival (Underwater Senses; Protection 2)", bonuses=(("TOUGH", 2),)),
    _race("Deep Spacer: Drakonkin", "Shard Vision (Radius and Extended Vision, Acute, Analytical)", "Born Gunslinger (Ranged Attack 4; Precise Attack; Quick Draw)", "Draconic Hide (Protection 3)", bonuses=(("TOUGH", 3),)),
    _race("Entropic Homunculus", "Godspark (Variable 8, Any Power, Slow)", "Improbable Existence (Immortality 5; Life Support)", "Chaos Incarnate (Luck Control 3; Luck 4)"),
    _race("Fen: Seelie", "Lawbound Immortality (Immortality 10; Aging Immunity)", "Yggdrasil Step (Dimension Travel 3)", "Trickster's Craft (Illusion 4; Concealment 2)"),
    _race("Fen: Calamite", "Cursed Mischief (Affliction 6, Uncontrolled, Side Effect)", "Yggdrasil Step (Dimension Travel 3)", "Fen Resilience (Regeneration 2; Aging Immunity)"),
    _race("Fen: Elementigo", "Chosen Element (Damage 8; Move Object 8; Create 6; Environment 4)", "Elemental Body (Immunity 5: Chosen Element)", "Yggdrasil Step (Dimension Travel 3)"),
    _race("Fen: Agnilos", "Narrative Weaving (Mind Reading 6, Limited)", "Schadenfreude Feast (Regeneration 4, Source)", "Author's Hand (Luck Control 2)", "Yggdrasil Step (Dimension Travel 3)"),
    _race("Fen: Malkav", "Madness Aura (Affliction 7, Perception Area)", "Spontaneous Absurdity (Create 8, Unreliable)", "Patchwork Mind (Mental Immunity, Half Effect)", "Yggdrasil Step (Dimension Travel 3)"),
    _race("Gamma Tauist: Tame", "Cyberframe (Protection 3; Enhanced Strength 2)", "Integrated Systems (Darkvision, Radio; Comprehend Machines)", "Redundant Hardware (Immunity 5)", bonuses=(("TOUGH", 3), ("STR", 2))),
    _race("Gamma Tauist: Extreme", "Full Conversion Body (Protection 6, Impervious 4; Enhanced Strength 4)", "Modular Chassis (Variable 4)", "Machine Physiology (Immunity 15, Biological Vectors)", bonuses=(("TOUGH", 6), ("STR", 4))),
    _race("Gamma Tauist: Forfeiter", "Total Mechanization (Fortitude Immunity; Protection 8)", "Distributed Existence (Growth 8+ or Remote Sensing 8)", "System Sovereign (Comprehend Machines; Mental Quickness 6)", bonuses=(("TOUGH", 8),)),
    *tuple(_race(name, *(COMMON_GELOSEAN + traits), bonuses=bonuses) for name, traits, bonuses in (
        ("Gelosean: Red", ("Blinding Speed (Speed 6; Quickness 4; Enhanced Agility 3)", "Rapid Strikes (Multiattack 3)", "Burnout Metabolism (Complication)"), (("AGL", 3), ("SPEED", 6))),
        ("Gelosean: Orange", ("Predator Senses", "Solidified Form (Protection 3; Enhanced Strength 2)", "Pounce (Leaping 2; Speed 2)"), (("TOUGH", 3), ("STR", 2), ("SPEED", 2))),
        ("Gelosean: Yellow", ("Acidic Body (Reaction Damage 6)", "Corrosive Touch (Weaken Toughness 6)", "Universal Digestion (Immunity 5)"), ()),
        ("Gelosean: Green", ("Master Shapeshifter (Morph 3)", "Metachrosis (Concealment 2)", "Malleable Anatomy (Elongation 3; Fluid)"), ()),
        ("Gelosean: Blue", ("Crystal Carapace (Protection 8, Impervious 6, Sustained)", "Solid Core (Enhanced Strength 3)", "Hardened Strikes (Damage 3)"), (("STR", 3),)),
        ("Gelosean: Purple", ("Pheromone Dominion (Affliction 8)", "Ultra Charisma (Enhanced Presence 4; Attractive 2)", "Emotional Read"), (("PRE", 4),)),
        ("Gelosean: Pink", ("Mass Shifting (Growth 8/Shrinking 12)", "Elastic Density (Protection 2, Scales with Growth)"), ()),
        ("Gelosean: Brown", ("Light Refraction (Visual Concealment 4)", "Ambush Adaptation (Hide in Plain Sight; Low-Light Vision)"), ()),
        ("Gelosean: Black", ("Deathless (Immortality 8, Biomass)", "Necrotic Recovery (Regeneration 6, Biomass)"), ()),
        ("Gelosean: Gray", ("Machine Possession (Affliction 8, Electronics)", "Magnetokinesis (Move Object 8, Metal)", "Electric Sympathy (Machines; Electricity Immunity)"), ()),
        ("Gelosean: White", ("Fission Selves (Summon 4, up to 6 duplicates)", "Hive Coordination (Teamwork, Set-Up)"), ()),
    )),
    _race("Human", "Baseline Versatility (Beginner's Luck; Jack-of-All-Trades)"),
    _race("Kangaa", "Marsupial Might (Leaping 3; Speed 2; Enhanced Strength 1, Limited)", "Pouch (Concealed Storage)", "Bush Survivor (Acute Smell; Low-Light Vision; Favored Environment)"),
    _race("Macairinic: Alder", "Hyper-Intellect (Enhanced Intellect 5; Mental Quickness 4)", "Arcane-Scientific Insight", "Radiation-Born (Radiation and Disease Resistance)", bonuses=(("INT", 5),)),
    _race("Macairinic: Impish", "Fiery Temper (Enhanced Fighting 2)", "War Dancer", "Desert Goblinoid", bonuses=(("FGT", 2),)),
    _race("Macairinic: Ichthi", "Aquatic Physiology", "Alchemical Refinement (Regeneration 3)", "Longevity Materia"),
    _race("Macairinic: Isharians", "Elerium Core (Damage 10)", "Living Stone (Protection 6; Life Support)", "Contained Pressure (Enhanced Strength 3; Regeneration 2)", "Core Instability (Complication)", bonuses=(("TOUGH", 6), ("STR", 3))),
    _race("Macairinic: Volanans", "Glider Physiology (Flight 3, Gliding)", "Skyborn Mind (Enhanced Intellect 2)", "Long-Lived", bonuses=(("INT", 2),)),
    _race("Macairinic: Bakk'Ur", "Particulate Form (Insubstantial 2; Flight 2)", "Entropy Feeding (Regeneration 4)", "Conceptual Resonance", "Fragile Field (Complication)"),
    _race("Nightwalker", "Engineered Culler (Immunity 12)", "Apex Hunter (Senses; Speed 2; Leaping 1)", "Terror Presence (Affliction 5)", "Predator Frame (Enhanced Strength 2; Enhanced Agility 2; Damage 2)", "Light Sensitivity", bonuses=(("STR", 2), ("AGL", 2), ("SPEED", 2))),
    _race("Ong-Ban Huna", "Gene-Forged Titan (Growth 3; Enhanced Strength 4)", "Bullet-Resistant Hide (Protection 5)", "Metallic Skeleton", "Redundant Organs", "Warborn Doctrine", bonuses=(("STR", 4), ("TOUGH", 5))),
    _race("Precursor", "Omnipotent Remnant (Variable 12)", "Beyond Time (Fortitude Immunity; Immortality 10; Time Travel)", "Physics Defiance (Teleport/Transform/Nullify Array)"),
    _race("Way Strider: Fictional Universe", "Narrative Existence (Variable 10)", "Psionic Anchor (Erasure/Rewriting Resistance)"),
    _race("Way Strider: Alternate Universe", "Parallel Origin (Native-universe powers)", "Entropic Residue (Detect Dimensional Anomalies)"),
    _race("Way Strider: Temporal", "Displaced Era (Anachronistic Knowledge)", "Temporal Static (Temporal Resistance)"),
    _race("Wolfman", "Hunting Form (Growth 2; Enhanced Strength 4; Damage 3; Speed 3; Protection 3)", "Perfected Nightwalker Stock", "Hidden Beast", "Hunter's Senses"),
    _race("Xzzykmnrglhtsjt", "Wallborn (Slithering; Sure-Footed; Shrinking 4)", "Current Eater (Electricity Source)", "Maintenance Instinct (Comprehend Machines; Technology Mastery)"),
    *tuple(_race(f"Medusan: {clan}", *(COMMON_MEDUSAN + (trait,))) for clan, trait in (
        ("Thunderstone", "Lightning descriptor; linked Dazed/Stunned Affliction 5"),
        ("Embershard", "Fire descriptor; Environment Heat or Secondary Effect"),
        ("Earthboulder", "Earth descriptor; Burst-Area Ground Strike"),
        ("Icepebble", "Cold descriptor; linked Hindered/Immobile Affliction 5"),
        ("Waterpike", "Water descriptor; Move Object 6 (Water); Swimming 3"),
        ("Acidrock", "Acid descriptor; linked Weaken Toughness 5"),
    )),
)

# Full rules text supplied for every race.  The compact declarations above keep
# automatic numeric bonuses readable; these complete traits are what the UI and
# receipts display.
FULL_RACE_TRAITS: dict[str, tuple[str, ...]] = {
    "Corvid": (
        "Avian Physiology (Flight 4, Wings; Senses 2 (Extended Vision 2))",
        "Merchant's Eye (Senses 3 (Detect Value, Acute, Analytical))",
        "Silver Tongue (Enhanced Advantages: Connected, Well-Informed, Contacts)",
    ),
    "Deep Spacer: Vanor": (
        "Void Adapted (Immunity 10: Life Support)",
        "Magnetic Grip (Movement 2 (Wall-Crawling 2), Limited to Ferrous Surfaces)",
        "Mineral Subsistence (Immunity 2: Starvation and Thirst, Regeneration 1, Source: Sunlight)",
    ),
    "Deep Spacer: Fon Fon": (
        "Bonded Devotion (Senses 2 (Acute, Tracking) Limited to Bonded Partner)",
        "Perfect Companion (Enhanced Presence 2; Enhanced Advantages: Attractive 2)",
        "Feline Grace (Enhanced Agility 1, Movement 1 (Safe Fall), Senses 2 (Low-Light Vision, Acute Hearing))",
        "Widow's Madness (Enhanced Fighting 4, Enhanced Advantages: Fearless, Activation: Loss of Bonded Partner, Uncontrolled)",
    ),
    "Deep Spacer: Ghilnacht": (
        "Living Computer (Quickness 8, Limited to Mental Tasks; Enhanced Intellect 4)",
        "Perfect Recall (Senses 2 (Eidetic Memory equivalent), Enhanced Advantages: Eidetic Memory, Speed of Thought)",
        "Data Interface (Comprehend 2 (Machines))",
    ),
    "Deep Spacer: Srodun": (
        "Hyper-Survivor (Immunity 20: Life Support, Starvation, Aging, Environmental Extremes)",
        "Unkillable Frame (Protection 6, Impervious; Regeneration 5)",
        "Stellar Tolerance (Immunity 10: Heat and Radiation Effects)",
    ),
    "Deep Spacer: Renpatar": (
        "Peak Perfection (Enhanced Strength 4, Enhanced Agility 4, Permanent, Innate)",
        "Effortless Feats (Leaping 2, Speed 2, Power-Lifting 2)",
        "Warrior's Longevity (Immunity 1: Aging, Limited to Half Effect)",
    ),
    "Deep Spacer: Qoaxl": (
        "Amphibious (Swimming 4, Immunity 3: Drowning, Cold, Pressure)",
        "Regenerative Limbs (Regeneration 4, Persistent)",
        "Leviathan-World Survival (Senses 3 (Ultra-Hearing, Acute Smell, Tracking) Limited to Underwater; Protection 2)",
    ),
    "Deep Spacer: Drakonkin": (
        "Shard Vision (Senses 6 (Radius Vision 2, Extended Vision 2, Acute, Analytical))",
        "Born Gunslinger (Enhanced Advantages: Ranged Attack 4, Precise Attack (Ranged, Cover), Quick Draw)",
        "Draconic Hide (Protection 3)",
    ),
    "Entropic Homunculus": (
        "Godspark (Variable 8 (Any Power), Slow)",
        "Improbable Existence (Immortality 5; Immunity 10: Life Support)",
        "Chaos Incarnate (Luck Control 3, Enhanced Advantages: Luck 4)",
    ),
    "Fen: Seelie": (
        "Lawbound Immortality (Immortality 10, Immunity 1: Aging, Both Limited: Void if Seelie Law is Broken)",
        "Yggdrasil Step (Movement 3 (Dimension Travel 3), Limited to Yggdrasil and Back)",
        "Trickster's Craft (Illusion 4 (Visual and Auditory), Concealment 2 (Normal Vision), Blending)",
    ),
    "Fen: Calamite": (
        "Cursed Mischief (Affliction 6 (Impaired, Disabled, Incapacitated), Resisted by Dodge, Uncontrolled, Side Effect: Harms Bystanders)",
        "Yggdrasil Step (Movement 3 (Dimension Travel 3), Limited to Yggdrasil and Back)",
        "Fen Resilience (Regeneration 2, Immunity 1: Aging)",
    ),
    "Fen: Elementigo": (
        "Chosen Element (Element Control Array: Damage 8, Move Object 8, Create 6, Environment 4; all Limited to One Specific Substance or Energy)",
        "Elemental Body (Immunity 5: Chosen Element)",
        "Yggdrasil Step (Movement 3 (Dimension Travel 3), Limited to Yggdrasil and Back)",
    ),
    "Fen: Agnilos": (
        "Narrative Weaving (Mind Reading 6, Limited to Emotional Pain and Desires)",
        "Schadenfreude Feast (Regeneration 4, Source: Suffering of Others Nearby)",
        "Author's Hand (Luck Control 2 (Force Rerolls), Limited to Causing Dramatic Misfortune)",
        "Yggdrasil Step (Movement 3 (Dimension Travel 3), Limited to Yggdrasil and Back)",
    ),
    "Fen: Malkav": (
        "Madness Aura (Affliction 7 (Entranced, Compelled, Controlled), Resisted by Will, Area: Perception, Limited to Inducing Random Behavior)",
        "Spontaneous Absurdity (Create 8, Reduced Duration (Sustained), Unreliable, Limited to Ridiculous Objects and Events)",
        "Patchwork Mind (Immunity 10: Mental Effects, Limited to Half Effect)",
        "Yggdrasil Step (Movement 3 (Dimension Travel 3), Limited to Yggdrasil and Back)",
    ),
    "Gamma Tauist: Tame": (
        "Cyberframe (Protection 3, Enhanced Strength 2)",
        "Integrated Systems (Senses 3 (Darkvision, Radio), Comprehend 2 (Machines))",
        "Redundant Hardware (Immunity 5: Disease, Poison, Starvation, Suffocation (partial), Sleep)",
    ),
    "Gamma Tauist: Extreme": (
        "Full Conversion Body (Protection 6, Impervious 4; Enhanced Strength 4)",
        "Modular Chassis (Variable 4 (Installed Cyberware), Slow, Limited to Technological Augments)",
        "Machine Physiology (Immunity 15: Fortitude Effects, Limited to Biological Vectors)",
    ),
    "Gamma Tauist: Forfeiter": (
        "Total Mechanization (Immunity 30: Fortitude Effects; Protection 8, Impervious)",
        "Distributed Existence (Growth 8 or higher, Permanent, Innate, Limited to Facility-Body Forms; alternately Remote Sensing 8 across own structure)",
        "System Sovereign (Comprehend 4 (Machines), Quickness 6 Limited to Computation)",
    ),
    "Human": ("Baseline Versatility (Enhanced Advantages: Beginner's Luck, Jack-of-All-Trades equivalent flexibility; no innate powers)",),
    "Kangaa": (
        "Marsupial Might (Leaping 3, Speed 2, Enhanced Strength 1, Limited to Kicks and Leaps)",
        "Pouch (Feature 1: Concealed Storage)",
        "Bush Survivor (Senses 2 (Acute Smell, Low-Light Vision), Enhanced Advantage: Favored Environment (Wilds))",
    ),
    "Macairinic: Alder": (
        "Hyper-Intellect (Enhanced Intellect 5, Quickness 4 Limited to Mental Tasks)",
        "Arcane-Scientific Insight (Enhanced Advantages: Inventor, Ritualist, Skill Mastery (Expertise), Eidetic Memory)",
        "Radiation-Born (Immunity 2: Radiation, Disease, Limited to Half Effect)",
    ),
    "Macairinic: Impish": (
        "Fiery Temper (Enhanced Fighting 2, Enhanced Advantage: Startle, All-Out Attack)",
        "War Dancer (Enhanced Advantages: Multiattack 2 on melee attacks, Instant Up, Agile Feint)",
        "Desert Goblinoid (Immunity 2: Heat, Radiation, Limited to Half Effect; Senses 1 (Darkvision Limited to Twilight))",
    ),
    "Macairinic: Ichthi": (
        "Aquatic Physiology (Swimming 3, Immunity 3: Drowning, Pressure, Cold)",
        "Alchemical Refinement (Regeneration 3; Immunity 2: Disease, Poison)",
        "Longevity Materia (Immunity 1: Aging, Removable (Dependent on Elixir Supply))",
    ),
    "Macairinic: Isharians": (
        "Elerium Core (Damage 10 (Energy Discharge), Uncontrolled without discipline, Side Effect on Failure (Feedback Damage))",
        "Living Stone (Protection 6, Impervious 4; Immunity 10: Life Support, Limited to Half Effect)",
        "Contained Pressure (Enhanced Strength 3; Regeneration 2, Source: Core Energy)",
        "Core Instability (treat as Complication: catastrophic detonation on loss of control)",
    ),
    "Macairinic: Volanans": (
        "Glider Physiology (Flight 3, Gliding Only, Wings)",
        "Skyborn Mind (Enhanced Intellect 2, Enhanced Advantages: Skill Mastery (Expertise: Astronomy or Engineering))",
        "Long-Lived (Immunity 1: Aging, Limited to Half Effect; Feature 1: Precise Altitude and Trajectory Estimation)",
    ),
    "Macairinic: Bakk'Ur": (
        "Particulate Form (Insubstantial 2 (Gaseous/Plasma), Permanent, Innate; Flight 2)",
        "Entropy Feeding (Regeneration 4, Source: Nearby Chaotic Events)",
        "Conceptual Resonance (Senses 3 (Detect Chosen Concept, Acute, Ranged))",
        "Fragile Field (treat as Complication: large chaotic events can destroy them outright)",
    ),
    "Nightwalker": (
        "Engineered Culler (Immunity 12: Disease, Sleep, Suffocation, Starvation Limited to Blood Diet)",
        "Apex Hunter (Senses 5 (Darkvision, Acute Smell, Tracking, Ultra-Hearing); Speed 2; Leaping 1)",
        "Terror Presence (Affliction 5 (Impaired, Disabled), Resisted by Will, Area: Perception, Limited to Fear)",
        "Predator Frame (Enhanced Strength 2, Enhanced Agility 2, Damage 2 (Claws and Fangs))",
        "Light Sensitivity (treat as Complication: Impaired in bright light)",
    ),
    "Ong-Ban Huna": (
        "Gene-Forged Titan (Growth 3, Permanent, Innate; Enhanced Strength 4)",
        "Bullet-Resistant Hide (Protection 5, Impervious 5, Limited to Ballistics for Impervious)",
        "Metallic Skeleton (Immunity 2: Critical Hits from Blunt Trauma; Enhanced Advantage: Improved Hold)",
        "Redundant Organs (Immunity 5: Critical Hits, Poison, Disease, Limited to Half Effect; Regeneration 2)",
        "Warborn Doctrine (Enhanced Advantages: Fearless, Second Chance (Intimidation))",
    ),
    "Precursor": (
        "Omnipotent Remnant (Variable 12 (Reality-Breaking Technology and Powers))",
        "Beyond Time (Immunity 30: Fortitude Effects; Immortality 10; Movement 3 (Time Travel 3))",
        "Physics Defiance (Alternate array: Teleport 10, Transform 10, Nullify 12 as sample expressions)",
    ),
    "Way Strider: Fictional Universe": (
        "Narrative Existence (Variable 10 (Powers of Origin Fiction), Limited to the Character's Source Material)",
        "Psionic Anchor (Immunity 5: Effects that Would Erase or Rewrite Them, Limited to Half Effect)",
    ),
    "Way Strider: Alternate Universe": (
        "Parallel Origin (Powers as per their native universe; build as any standard character concept)",
        "Entropic Residue (Senses 2 (Detect Dimensional Anomalies, Ranged))",
    ),
    "Way Strider: Temporal": (
        "Displaced Era (Enhanced Advantages and Expertise reflecting anachronistic knowledge; Feature 2: Knowledge of Past or Future Events)",
        "Temporal Static (Immunity 2: Temporal Effects, Limited to Half Effect)",
    ),
    "Wolfman": (
        "Hunting Form (Alternate Form Array, Free Action shift: Growth 2, Enhanced Strength 4, Damage 3 (Claws and Jaws), Speed 3, Protection 3, Enhanced Advantage: Multiattack 2 on claw attacks)",
        "Perfected Nightwalker Stock (Immunity 10: Disease, Sleep, Starvation Limited to Blood Diet, UV Light)",
        "Hidden Beast (Morph 1 (Own Human Guise), Innate; Feature 1: Undetectable as Wolfman while shifted)",
        "Hunter's Senses (Senses 4 (Darkvision, Acute Smell, Tracking))",
    ),
    "Xzzykmnrglhtsjt": (
        "Wallborn (Movement 2 (Slithering, Sure-Footed 1); Shrinking 4, Innate, Permanent or Sustained per concept)",
        "Current Eater (Immunity 2: Starvation and Thirst, Limited: Requires Electricity Source; Regeneration 1, Source: Electricity)",
        "Maintenance Instinct (Comprehend 2 (Machines), Enhanced Advantages: Skill Mastery (Technology), Hide in Plain Sight Limited to Structures)",
    ),
}

_GELOSEAN_COMMON_FULL = (
    "Amorphous Cell (Insubstantial 1 (Fluid), Morph 1 (Single Shape Class); Immunity 5: Critical Hits, Disease, Poison)",
    "Protein Hunter (Regeneration 2, Source: Consumed Biomass)",
)
_GELOSEAN_COLOR_FULL = {
    "Red": ("Blinding Speed (Speed 6, Quickness 4, Enhanced Agility 3)", "Rapid Strikes (Enhanced Advantage: Multiattack 3 on unarmed attacks)", "Burnout Metabolism (Fades on extended exertion; treat as Complication)"),
    "Orange": ("Predator Senses (Senses 5 (Acute Smell, Tracking, Low-Light Vision, Ultra-Hearing))", "Solidified Form (Protection 3, Enhanced Strength 2)", "Pounce (Leaping 2, Speed 2)"),
    "Yellow": ("Acidic Body (Damage 6, Reaction (When Touched), Limited to Grappling or Sustained Contact)", "Corrosive Touch (Weaken Toughness 6, Affects Objects)", "Universal Digestion (Immunity 5: Poison, Disease, Ingested Hazards)"),
    "Green": ("Master Shapeshifter (Morph 3 (Humanoid Forms))", "Metachrosis (Concealment 2 (Normal Vision), Blending, Passive)", "Malleable Anatomy (Elongation 3, Insubstantial 1 (Fluid), Innate)"),
    "Blue": ("Crystal Carapace (Protection 8, Impervious 6, Sustained, Activation: Move Action)", "Solid Core (Enhanced Strength 3, Immunity 2: Critical Hits)", "Hardened Strikes (Damage 3 (Crystalline Fists))"),
    "Purple": ("Pheromone Dominion (Affliction 8 (Entranced, Compelled, Controlled), Resisted by Will, Cumulative, Sense-Dependent (Smell))", "Ultra Charisma (Enhanced Presence 4, Enhanced Advantages: Attractive 2, Fascinate (Persuasion))", "Emotional Read (Senses 2 (Detect Emotional State, Acute))"),
    "Pink": ("Mass Shifting (Growth 8 and Shrinking 12 in a Dynamic Array, Sustained)", "Elastic Density (Protection 2, Scales with Growth)"),
    "Brown": ("Light Refraction (Concealment 4 (All Visual Senses), Passive)", "Ambush Adaptation (Enhanced Advantage: Hide in Plain Sight; Senses 1 (Low-Light Vision))"),
    "Black": ("Deathless (Immortality 8, Limited: Requires Suitable Biomass Nearby)", "Necrotic Recovery (Regeneration 6, Source: Biomass)"),
    "Gray": ("Machine Possession (Affliction 8 (Dazed, Compelled, Controlled), Resisted by Will or Tech DC, Limited to Electronics; alternately Remote Sensing 4 via devices)", "Magnetokinesis (Move Object 8, Limited to Metal, Perception Range)", "Electric Sympathy (Comprehend 3 (Machines), Immunity 5: Electricity)"),
    "White": ("Fission Selves (Summon 4, Multiple Minions 2 (up to 6 duplicates), Mental Link, Active, Feedback, Limited: Duplicates Split Total Mass)", "Hive Coordination (Enhanced Advantages: Teamwork, Set-Up, applies among duplicates)"),
}
for _color, _traits in _GELOSEAN_COLOR_FULL.items():
    FULL_RACE_TRAITS[f"Gelosean: {_color}"] = _GELOSEAN_COMMON_FULL + _traits

_MEDUSAN_COMMON_FULL = (
    "Towering Warrior (Growth 2, Permanent, Innate)",
    "Serpent Crown (Senses 3 (Radius Vision, Danger Sense); Feature 1: Snakes Betray True Emotions as Complication or tell)",
    "Clan Energy Streams (Damage 7 (Snake-Mouth Beams), Ranged, Multiattack 2, Energy Descriptor by Clan)",
    "Petrifying Gaze (Affliction 9 (Hindered, Immobile, Transformed to Stone), Resisted by Fortitude, Perception Range, Sense-Dependent (Vision), Limited to Skilled Elders)",
    "Serpentine Maw (Feature 2: Swallow Prey Whole; Damage 2 (Bite))",
)
_MEDUSAN_CLAN_FULL = {
    "Thunderstone": "Thunderstone: Beams and gaze effects carry the Lightning descriptor; add Affliction 5 (Dazed, Stunned) Linked to beam damage",
    "Embershard": "Embershard: Fire descriptor; add Environment 2 (Heat) or Secondary Effect on beams",
    "Earthboulder": "Earthboulder: Earth descriptor; add Burst-Area Ground Strike (Damage 6, Area: Line)",
    "Icepebble": "Icepebble: Cold descriptor; add Affliction 5 (Hindered, Immobile) Linked to beams",
    "Waterpike": "Waterpike: Water descriptor; add Move Object 6 Limited to Water and Swimming 3",
    "Acidrock": "Acidrock: Acid descriptor; add Weaken Toughness 5 Linked to beams",
}
for _clan, _trait in _MEDUSAN_CLAN_FULL.items():
    FULL_RACE_TRAITS[f"Medusan: {_clan}"] = _MEDUSAN_COMMON_FULL + (_trait,)

RACES = tuple(
    RaceTemplate(race.name, FULL_RACE_TRAITS.get(race.name, race.traits), race.bonuses, race.default_weight)
    for race in RACES
)


def race_parent(name: str) -> str:
    """Return the hierarchical probability group for a race option."""
    return name.split(":", 1)[0].strip() if ":" in name else name


RACE_GROUPS: dict[str, tuple[RaceTemplate, ...]] = {}
for _race_template in RACES:
    _parent = race_parent(_race_template.name)
    RACE_GROUPS[_parent] = (*RACE_GROUPS.get(_parent, ()), _race_template)


MANUFACTURER_ALIASES = {
    "hexatech neural": "Hexatek Neural",
    "granreale foods": "Granrealé Foods",
}

# Numeric effects are intentionally conservative. Everything in ``note`` is
# printed, but only explicit values below modify the generated sheet.
MANUFACTURER_RULES: dict[str, dict[str, object]] = {
    "Yoshitake Foods": {"price_cap": 8, "note": "Prolonged use: DC 12 Fortitude vs illness."},
    "Granrealé Foods": {"price_cap": 6, "note": "Food makes a DC 8 rot check per day."},
    "Luther Trading": {"bonuses": {"FORT": 2}, "note": "Immune to spoilage; half nutritional value."},
    "Phage": {"fixed_price": 50, "note": "+2 social checks for 12 hours; weekly DC 10 addiction."},
    "Auburn Biovats": {"fixed_price": 15, "note": "+1 to all rolls for 12 hours."},
    "OmniMart": {"price_cap": 10, "note": "10% malfunction chance (DC 10 Equipment)."},
    "RadioJack": {"price_multiplier": 2.0, "note": "Unaffected by power loss or EMP; comms cannot be jammed."},
    "Glint Systems": {"note": "Self-repairs when damaged or destroyed."},
    "Sable Exchange": {"effect_rank_delta": 2, "note": "Illegal in most systems."},
    "Ardent Dynamics": {"bonuses": {"ATTACK": 1, "DAMAGE": -1}},
    "Caliber Collective": {"price_multiplier": 0.1, "note": "10% chance of permanent failure."},
    "Tarkov Systems": {"note": "Never jams; Quick Draw requires a move action."},
    "Eidolon Arms": {"bonuses": {"STEALTH": -5}, "note": "Ignores energy resistance; immunity becomes resistance."},
    "Kythera Forge": {"note": "Critical range +1; DC 12 Technology maintenance."},
    "Brightcorp": {"bonuses": {"ATTACK": 1}, "note": "Corrects aim after one miss; hacked systems may turn hostile (Will DC 18)."},
    "Pan-Odyne Defense": {"price_multiplier": 0.9, "bonuses": {"STEALTH": -1}},
    "VoxShield Technologies": {"note": "Broken shields fail only for the current combat."},
    "AegisCor Resonants": {"note": "Rebounds attacks; active only while Defending."},
    "Kasparov Industries": {"bonuses": {"STR": 2}, "note": "EMP hit causes one fatigue level."},
    "Silkline Apparel": {"bonuses": {"TOUGH": -1, "SOCIAL": 5}},
    "Black Talon Outfitters": {"note": "Includes a covert weapon or tool feature."},
    "Vyral Systems": {"note": "Resistant to EMP and hacking."},
    "Hexatek Neural": {"price_multiplier": 0.75, "note": "Reduced installation and maintenance cost."},
    "Auron Yumitech": {"note": "DC 16 Technology: +5 rank for one hour."},
    "Ascendigo Bionics": {"price_multiplier": 10.0, "effect_rank_multiplier": 3, "note": "Indestructible and cannot be hacked."},
    "IronHalo Cybernetics": {"note": "Takes half damage from equipment attacks and EMP."},
    "Liminal Incorporated": {"note": "DC +15 to detect with standard scans."},
    "Johnston-Tanaka Technologies": {"capacity_multiplier": 0.5, "note": "Full conversions require specialized maintenance and energy."},
    "HelixGene Industries": {"note": "Temporary treatment; 10% failure rate."},
    "Aeon Spire Labs": {"note": "Subsequent treatments increase instability risk."},
    "Vellum Bioworks": {"note": "Symbiote may exert influence if neglected (Will DC 12)."},
    "Paragon Genome": {"required_race": "Human", "note": "Functions only on pure human DNA."},
    "Helix Motors": {"note": "Fast but unreliable; daily DC 10 failure check."},
    "Apex Aerodyne": {"price_multiplier": 5.0, "bonuses": {"DEFENSE": 2}, "note": "Double vehicle speed."},
    "Zeroth Transit": {"bonuses": {"SPEED": -2}, "note": "Ignores difficult terrain."},
    "Nadir Robotics": {"note": "AI Intellect 6; may resist commands (Will DC 10)."},
    "YelloGlas": {"note": "Cheap communal transport; Gelosean pilots only."},
    "Un-Dyne Industries": {"note": "Ultra-durable transport with heavy armament."},
    "Obsidian Automata": {"bonuses": {"SOCIAL": 2}, "note": "Risk of developing autonomy."},
    "MechaDyne Solutions": {"note": "+2 Strength tasks; double maintenance cost."},
    "SilicoCore": {"bonuses": {"INITIATIVE": 2}, "note": "5% instability chance."},
    "Apex Robotics": {"price_multiplier": 0.5, "note": "Network-linked; a shared hack affects all linked units."},
    "Loxon Therapeutics": {"note": "Acts immediately, fades quickly, and may cause dependency."},
    "Calyx Medica": {"note": "Adaptive nanotechnology delivers and regulates treatment."},
    "Vestra Vitae": {"note": "Luxury regenerative and longevity treatment."},
}

# Makers which have distinct rules in different product families.
MANUFACTURER_SECTION_RULES: dict[tuple[str, str], dict[str, object]] = {
    ("Brightcorp", "Inventory"): {"bonuses": {"COORDINATION": 2}, "note": "Advanced autonomous tactics; continues last orders if control is lost."},
    ("Brightcorp", "Bionics"): {"note": "Integrated robotic warfare system; hostile behavior possible if hacked."},
}

AMMO_ALIASES = {
    "9x19mm": "9mm Cartridge",
    "7.62x51mm NATO": "7.62mm NATO Cartridge",
    "Formulated Toxin Reservoir": "Toxin Reservoir",
}
