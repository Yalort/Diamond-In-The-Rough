# CTP500 Live Gear and Minion Printer

Current release: **v1.2.4**

## Download

Download the versioned standalone Windows application from the
[latest GitHub release](https://github.com/Yalort/Diamond-In-The-Rough/releases/latest).
The `.exe` bundle includes Python and the application dependencies, so it does
not require a separate Python installation. GitHub also provides source ZIP and
TAR archives with each release.

The app checks the canonical GitHub Releases page for stable versions on startup.
After confirmation it opens that page in your browser; executable replacement is
never performed automatically. Use **Check for updates** to check again at any time.

## Release builds

Push a tag matching the application version (for example, `v1.2.4`) to run the
Windows release workflow. The workflow runs the test suite, verifies that the tag
matches `ctp500_app/_version.py`, and publishes a versioned standalone executable
and SHA-256 checksum to the corresponding GitHub Release.

A Windows desktop app that reads a user-selected Google Sheets gear dataset,
lets you search and filter its gear, and prints category-aware tags on a paired
CTP500 thermal printer. The Character Creator tab also builds seeded MnM minion
statblocks from the live catalog, stores editable snapshots locally, and prints
complete character receipts.

## Merchants

The **Merchants** tab saves full shops and reusable stock bundles. Generate
catalog-backed, seeded stock from wealth and colored Guarantee/Favor/Avoid/
Forbid tags, then lock or edit individual snapshot lines. Layered price rules,
an M&M-flavored haggling assistant, a persistent sales ledger, receipt printing,
catalog-change reconciliation, and encounter merchant links keep shopkeeping
work in the same offline-safe workspace. Merchant stock is grouped into
category-specific expandable tables, with selector-backed profile controls and
wealth-scaled inventory generation.

## Requirements

- Windows 10 or 11
- Python 3.11 or newer
- A paired CTP500-compatible RFCOMM Bluetooth printer
- Internet access to an anonymously readable Google Sheets dataset

Install Pillow:

```powershell
python -m pip install -r requirements.txt
```

Alternatively, install the application from this checkout:

```powershell
python -m pip install .
ctp500-printer
```

Run the app from this directory:

```powershell
python CTP500_GUI_app_Github_Export.py
```

## Use

1. Enter the printer's Bluetooth address in `AA:BB:CC:DD:EE:FF` format and
   select **Connect**. The address is remembered in your Windows application
   data folder after a successful connection.
2. Search, filter, or sort the catalog. Ctrl-click or Shift-click to select
   multiple items.
3. Choose a copy count and select **Queue selected**. Double-clicking an item
   puts one copy at the front of the pending queue.
4. Pending jobs may be canceled. Canceling a printing job lets the current tag
   finish, then cancels its remaining copies.
5. A printer error pauses the queue. Reconnect if necessary, then use **Retry**
   or **Remove** on the failed job.

## Character Creator

1. Choose a Power Level, role, race or weighted random race, credit budget, and
   weapon preference. Quality and Quantity each have an enable checkbox; when
   disabled, their slider contributes no generation weighting or continuation
   adjustment. The budget slider includes clickable income-strata landmarks
   that set the corresponding budget, while the entry accepts an exact value.
2. Adjust category probabilities in the quick panel. Values are initial chance,
   continuation chance, and maximum count. Use **Race chances** to exclude races
   or change their random weights. Parent races are rolled first using the
   folded parent rows; expanded subrace weights are then applied only within
   the selected parent.
3. Select **Generate**. The seed can be retained to reproduce the same base roll.
   Multi-manufacturer catalog rows resolve to one persistent manufacturer.
   A manufacturer value of `All` expands to every manufacturer found in that
   item's catalog category, then resolves and persists one of them normally.
   Generation also adds a lootable Galactic Credit Chip holding a seeded random
   0–100% of the budget left after equipment purchases. Generated skill chips
   resolve to a seeded skill and show its bonus. Any generated chip that needs
   a chipslot force-installs the socket and its neuralware prerequisite when
   necessary. Generation does not reject bionics for exceeding capacity.
   Instead, each point (or fraction) over capacity causes CyberPsychosis and a
   -5 penalty to every defense; the warning is printed beside the character's
   race.
4. Edit base stats, remove/lock/reroll equipment, or add selected rows from the
   Gear Catalog, then recalculate. **Ignore add limits** permits catalog items
   and forced prerequisites even when they exceed admission limits. Their full
   price, capacity, corruption, and modifiers still apply; overspending raises
   the character budget to match. Capacity and corruption are shown in the live
   preview. Blue item names toggle the details included in the character receipt.
   Right-clicking a name marks it orange and uses an essential-info print mode
   that omits price, capacity, adjusted values, descriptions, and manufacturer
   effect notes. The generated-item controls can toggle all details or print
   selected items as standalone gear tags. **Toggle orange (x)** opens all
   orange essential-info items when their states are mixed, then closes them
   all on the next press. **All orange (a)** marks every equipment tag orange.
   Weapon entries show their calculated to-hit
   modifier, including compatible neuralware-assisted smartgun bonuses.
5. Save snapshots to the local character database, load or duplicate them later,
   and queue a full statblock using **Print**. Saved snapshots work without a live
   catalog connection.

Character data is stored in `%APPDATA%\CTP500GearPrinter\characters.db`.
Generator defaults and race weights are stored beside it in `config.json`.

## Encounter Tracker

The **Encounter Tracker** tab is a local MnM 3e GM workspace. Campaigns hold a
party roster, tags, templates, recurring chronology, and archived sessions.
Templates can freeze saved-character snapshots or use advanced recipe slots
for saved, generated, and ad-hoc opponents. Quick generation uses the visible
PL threat heuristic and the selected campaign party as its default party size
and average PL. Generated encounter combatants inherit the Character Creator's
enabled races, race weights, parent-race weights, and exclusions. Advanced
recipe slots may explicitly override those defaults through their individual
`GeneratorSettings`.

Gamma Tauists receive racial bionics in addition to ordinary generated gear.
Tame characters add 3 to their normal bionic total. Extreme characters add 3
and then double the resulting total; Forfeiters add 5 and then double it. Only
the implants added by this racial formula have zero adjusted price and zero
bionic-cap usage, representing the population's extreme level of pre-existing
modification.

During play, the tracker becomes a one-screen combat console: the initiative
roster stays on the left and the selected combatant fills one receipt-style
encounter sheet. The sheet includes live encounter stats, turn state, actions,
conditions, resources, runtime modifiers, notes, and the complete frozen
character output used by Character Creator. Equipment names use the same
click-to-expand details as the generator. Right-click the combatant name,
actions, conditions, resources, modifiers, or equipment tags for their relevant
combat actions; right-click blank sheet space for general character actions.
Campaign/template/session management collapses
automatically when an encounter starts and can be reopened from **Management**.
Loot, objectives, hazards, environment modifiers, and history share the
non-tabbed **Encounter Details** tray.

Use individual or grouped initiative, manual initiative values,
delay/ready/hold/skip controls, action-use tracking, bruises, structured timed
conditions, save-ends checks, resources, objectives, hazards, round timers,
zones, and environment modifiers. **+ Label** adds a statless named turn at its
own initiative for player turns or timed events. The label prompt remembers the
eight most recent label/initiative pairs; double-click one to add it immediately.
Double-click a label in the roster to edit it. Choose an action and a dedicated target,
optionally enter either d20 result, then preview and explicitly apply or only
log the result without opening a chain of dialogs. Blank dice fields are rolled
by the app. Conditions and other edits are available from the sheet context
menus. Every combatant receives an Unarmed action calculated from final STR,
including bionic Strength and explicit bionic unarmed-strike bonuses. Weapon,
bionic Damage, bionic Affliction, and active bionic utility actions are derived
from each frozen character snapshot; custom actions use a
single editor and can be published to the saved character's reusable action
library.

The tracker also resolves custom damage-over-time conditions. **Burning** and
**Poisoned** last 1d6 turns, deal a normal Toughness-resisted damage effect at
half the source attack's rank each tick, and reset to six ticks when reapplied.
Poisoned additionally makes an escalating Fortitude save and progresses through
Hindered, Immobile, and Dying on failures. **Bleeding** makes an escalating
Stamina save on each turn and applied attack hit, adding a bruise on failure.
**Diseased** weakens a random attribute each tick until a Persistent Healing
action removes the disease and all of its tracked penalties. **Dying** makes the
same escalating Stamina save and becomes Dead on failure; Dead defeats the
combatant unless its traits, equipment, or actions contain Immortality.

Select one of these conditions and press **Tick** to resolve it manually. Turn
and attack ticks present each required d20 with a **Roll** button; cancelling
leaves the encounter unchanged. Weapon actions apply custom conditions only
when Special Notes explicitly name them. Notes may include an application
chance such as `25% chance of Burning`; a named condition without a percentage
is guaranteed on a confirmed hit. Generic `Persistent damage N rounds` text is
not treated as a custom condition.

Generated ammunition quantities follow the largest matching weapon clip. A
weapon action consumes its numeric **Burst** amount when both ammunition and a
burst are listed; weapons missing either value remain unlimited. Drugs,
grenades, poisons, explosives, stims, doses, and injectors also become linked
consumable actions. Generated drugs stack in quantities of 1–3 and generated
grenades in quantities of 1–2. Double-click an action to consume its linked inventory, or
drag it onto an initiative entry to roll, apply, consume, and log a quick use.
Dropping on a statless label consumes and logs without resolving an attack.
Insufficient actions remain usable but appear red. Shift-click an action to
expand and reveal its originating equipment.

Double-click any combatant in the initiative roster to resolve an incoming
attack that has no tracked attacker or action card. First enter the attack's
final to-hit total and choose DODGE or PARRY; misses are logged without opening
damage resolution. Hold **Shift** while double-clicking to bypass the hit check
for area effects or already-confirmed hits. The damage prompt accepts a damage
type, effect rank, and resistance d20. Hover any attack-prompt field and use the
mouse wheel to adjust numbers or cycle dropdown choices. It applies the Macairin damage
chart's shield and armor interactions before resistance, including ignored,
blocked, and destroyed protection; Arc conductivity, poison sealing, EMP
hardening/electronics checks; damage or affliction degrees; continuous acid;
and Fire/Gravity/Void secondary effects. Protection destruction is encounter
state only and never rewrites the saved character. Descriptive catalog text is
used to infer non-conductive, sealed, hardened, and electronic traits, and the
prompt displays those inferences before applying the result. Plasma backlash
and Sonic wielder damage are reported but cannot be applied because this
workflow intentionally has no attacker.

Catalog gear can be searched and added without leaving the encounter screen,
or removed from a combatant during a session. The
snapshot immediately recalculates budgets, corruption, capacity,
CyberPsychosis, defenses, smartgun attacks, skill chips, and prerequisites,
without changing the saved source character. Source changes are indicated and
require an explicit refresh. Completion likewise asks before publishing each
linked combatant back to its source.

Loot is staged from combatant gear and credit chips, revealed on defeat (or by
GM command), and can be edited or excluded before printing. Initiative lists,
combatant cards, action cards, condition reminders, loot receipts, and encounter
summaries all use the existing thermal-print queue. Every session edit is
atomically autosaved with before/after state; **Undo** and **Redo** cover combat,
gear, loot, resource, objective, environment, and note changes. Restarting the
app resumes the last committed session state.

Encounter shortcuts are active only while the Encounter Tracker tab is open
and are suppressed while typing in an entry, combobox, or notes field. Use
**Space** for the next turn; **I/S** to roll or set initiative; **Enter** to
preview the selected action and then apply a pending hit; **B/Shift+B** to add or remove a bruise; **C** for
a condition; **D** for defeated; **R/H/K** for ready, hold, or skip; **1/2/3**
for standard, move, or reaction use; **L** to reveal selected loot; **O** to
advance an objective; **F6/F7** to focus the roster or target selector;
**Escape** to clear a pending action preview; and **Ctrl+Z/Ctrl+Y** for
undo/redo. **F1** or the
**Hotkeys** button displays the reference in the app.

Campaigns, templates, sessions, events, and character action libraries use
versioned tables in the same `characters.db` database. Existing character rows
are not migrated or rewritten by the encounter schema.

The catalog refreshes every 15 seconds. A failed refresh leaves the last good
catalog visible. The last successful catalog is also cached in the application
data folder, so catalog browsing and generation remain available after an
offline restart. The status bar identifies live, refreshing, cached, and
unavailable states. The print queue is intentionally discarded when the app exits.

Use **Data Management** to export or restore a ZIP backup containing the local
character/encounter database, settings, and cached catalog. Restores are
validated before use and create a pre-restore safety backup. Diagnostic logs are
rotated under `%APPDATA%\CTP500GearPrinter\logs` and can be opened from the same
dialog.

## Catalog configuration

Choose **Data Management** and save a public Google Sheets share link to select
the catalog. The link is stored only in your local application settings; no
personal dataset or share link is bundled with the public release. The dataset
must preserve the expected category tabs and column headers. Included category
gids, polling interval, printer width, timeouts, and raster band size are
centralized in `ctp500_app/config.py`; `Packing Sheet` is not included.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The automated suite uses fake HTTP responses and a fake printer; it does not
send anything to Bluetooth hardware.
# Procedural physical profiles

Character previews and receipts include a deterministic, top-to-bottom physical
profile. Base STR, STA, AGL, FGT, AWE, DEX, INT, and PRE values select phrases
from explicit threshold tiers; race, installed bionics, and genemods add visible
morphology. The same character seed and state always produce the same wording.

Systems that author transformations can place private state in
`character.settings["_transformation_flags"]` (or `"transformation_flags"`) as a
mapping of flag names to booleans. Supported flags are `amorphous`, `aquatic`,
`avian`, `bestial`, `crystalline`, `cybernetic`, `full_conversion`, `gaseous`,
`giant`, `miniature`, `mutated`, `serpentine`, `shapeshifter`, and `undead`.
Flags affect observable prose but their names and values are never printed.
