# Changelog

All notable changes to this project are documented here. Releases follow
[Semantic Versioning](https://semver.org/).

## [1.2.4] - 2026-11-02

### Changed

- Prepared the first executable release in the new repository with a tag-validated Windows build that publishes a versioned executable and SHA-256 checksum.
- Added `AGENTS.md` to the repository ignore rules.

## [1.2.3] - 2026-11-02

### Security

- Removed automatic executable download and replacement; update discovery now opens only the canonical `Yalort/Diamond-In-The-Rough` release page.
- Added strict member, aggregate-size, compression-method, and compression-ratio limits to backup imports, and stream validated members through one staged copy.
- Bounded catalog downloads, rows, fields, item counts, cache files, and printable image dimensions to prevent resource-exhaustion attacks.
- Added repository ignore rules for Python bytecode, local databases, settings, logs, secrets, build output, and temporary update/restore artifacts.

### Changed

- Prepared the project for a history-free, source-only publication that excludes all tracked bytecode, including orphaned compiled AI modules.

## [1.2.2] - 2026-11-01

### Changed

- Split the encounter action workspace into four independently scrollable, resizable panes: Weapons / Actions, Bionics, Inventory, and Racial Abilities / Special Powers.
- Populate bionics and carried equipment directly from the selected character, including quantity, category, and effect descriptions.
- Show racial traits and manually authored special actions, and allow double-clicking linked gear or powers to select their combat action.
- Keep weapon ammunition type, quantity, attack cost, and the action resolver available in the combat workspace.
- Preserve the panel divider positions when resizing and the per-pane selection and scroll position when refreshing character state.
- Add regression coverage for equipment categorization, empty panels, race traits, and resizable quadrant layout.

## [1.2.1] - 2026-11-01

### Fixed

- Restored a permanent Show Manager / Hide Manager control in the encounter header, so campaign and session management can always be reopened after starting an encounter.
- Made manager-toggle text reflect the actual panel state and anchored panel re-expansion before the initiative roster.
- Added regression tests for reopening and repeated manager-panel toggles.

## [1.2.0] - 2026-11-01

### Changed

- Rebuilt the encounter screen as a three-pane workspace: initiative roster, compact character inspector, and action/target resolution.
- Moved generation, campaign management, merchant linking, printing, and shortcuts into compact header menus.
- Placed round advancement in the header, initiative editing directly on roster values, and target selection directly in the roster without losing the acting character.
- Replaced the oversized embedded character sheet with a compact inspector for defenses, bruises, conditions, other resources, and action economy; retained a live full-sheet window.
- Added per-weapon ammunition type, available quantity, per-attack cost, empty indicators, and inline adjustment beside weapon actions.
- Updated action resolution to account for ammo expenditure on both hit and miss, with insufficient-ammo checks on preview, special, and drag-to-target actions.
- Added a collapsible encounter event history and action/character overflow menus.
- Added regression coverage for distinct ammo types, restocking, untracked costs, and missed-shot expenditure.

## [1.1.15] - 2026-11-01

### Changed

- Introduced reusable searchable catalog and saved-record pickers to remove Gear Catalog tab switching and exact-name/index prompts.
- Consolidated quick encounter generation, ad-hoc combatants, loot, resources, objectives, and environment creation into validated single forms.
- Exposed encounter character actions and action selection directly on the combat console rather than relying solely on right-click text tags.
- Hid advanced character probability and stat controls behind a disclosure button.
- Replaced merchant pricing-rule strings, haggling settings, sales, and item overrides with structured forms.
- Displayed merchant names in the saved-merchant list and gave stock-rule chips explicit edit/remove controls.
- Changed merchant reconciliation Cancel to leave stock untouched rather than deleting it.
- Added regression tests for form value validation.

## [1.1.14] - 2026-08-09

### Security

- Pointed update checks at the current release repository and require a safe executable asset name plus GitHub-published SHA-256 integrity metadata before installing an update.
- Added bounded, atomic update downloads that discard failed or oversized files.

## [1.1.13] - 2026-08-09

### Changed

- Removed the bundled personal catalog dataset reference. Public-release users now save their own public Google Sheets share link in Data Management.

## [1.1.12] - 2026-08-09

### Fixed

- Corrected the GitHub Releases update source and treat repositories with no releases as up to date instead of reporting a 404 error.

## [1.1.11] - 2026-08-09

### Added

- Added GitHub Releases update checks and a confirmed download/install/restart flow for Windows bundles.

## [1.1.10] - 2026-08-09

### Changed

- Moved encounter character equipment into an always-visible Gear section outside Character MetaData.

## [1.1.9] - 2026-08-09

### Changed

- Grouped encounter resources, modifiers, notes, and character snapshots under a collapsible Character MetaData section.

## [1.1.8] - 2026-08-09

### Changed

- Heroic encounter combatants are underlined in the initiative roster.
- Moved the character physical profile to an expandable section at the bottom of the sheet; it prints only when expanded.

## [1.1.7] - 2026-08-09

### Changed

- Guaranteed weapons now prefer affordable options and cap any necessary budget-overflow weapon at one-eighth of the character budget.

## [1.1.6] - 2026-08-09

### Added

- Recent initiative labels now support multi-selection and batch addition.

## [1.1.5] - 2026-08-09

### Changed

- Encounter Quick Generate now remembers party size and average PL selections for future use.

## [1.1.4] - 2026-08-09

### Added

- Added Guarantee weapon controls to character and encounter generation, ensuring generated characters receive a weapon when enabled.

## [1.1.3] - 2026-07-22

### Changed

- Split character Toughness displays into color-coded base, armor, shield, and combined cumulative values.

## [1.1.2] - 2026-07-22

### Changed

- Added live search to merchant specialties, affiliations, category filtering, and grouped stock lists.

## [1.1.1] - 2026-07-22

### Changed

- Reworked merchant stock into category-specific tables, visual generation-rule chips, and selector-backed merchant controls.
- Increased wealth-tier stock generation and added category-specific quantity ranges.

## [1.1.0] - 2026-07-21

### Added

- Merchant and reusable stock-bundle workspace with catalog snapshots, seeded generation, colored stock-rule tags, editable quantities, print sheets, and local persistence.
- Layered merchant pricing, M&M-flavored haggling outcomes, transaction audit history, catalog reconciliation, and encounter merchant links.

## [1.0.0] - 2026-07-19

### Added

- Live Rough Diamond gear catalog browsing, filtering, and offline caching.
- Bluetooth CTP500 thermal-printer connection and resilient print queue.
- Seeded character and minion generation with local snapshot storage.
- Campaign, encounter, initiative, combat, condition, loot, and history tools.
- Backup, restore, diagnostics, keyboard shortcuts, and 129 automated tests.

[1.0.0]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.0.0
[1.1.0]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.0
[1.1.1]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.1
[1.1.2]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.2
[1.1.3]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.3
[1.1.4]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.4
[1.1.5]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.5
[1.1.6]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.6
[1.1.7]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.7
[1.1.8]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.8
[1.1.9]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.9
[1.1.10]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.10
[1.1.11]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.11
[1.1.12]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.12
[1.1.13]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.13
[1.1.14]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/releases/tag/v1.1.14

[1.1.15]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/compare/v1.1.14...v1.1.15

[1.2.0]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/compare/v1.1.15...v1.2.0

[1.2.1]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/compare/v1.2.0...v1.2.1

[1.2.2]: https://github.com/Yalort/RoughDiamondGearPrinter-EncounterTracker/compare/v1.2.1...v1.2.2

[1.2.3]: https://github.com/Yalort/Diamond-In-The-Rough/compare/v1.2.2...v1.2.3

[1.2.4]: https://github.com/Yalort/Diamond-In-The-Rough/compare/v1.2.3...v1.2.4
