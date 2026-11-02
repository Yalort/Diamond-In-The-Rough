from __future__ import annotations

import copy
import json
import logging
import os
import queue
import random
import re
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from uuid import uuid4

from ._version import __version__
from .catalog import CatalogClient, CatalogError, filter_items, manufacturer_options, natural_key
from .catalog_cache import load_catalog_cache, save_catalog_cache
from .backup import BackupError, export_backup, restore_backup, validate_backup
from .character_data import RACES, RACE_GROUPS, race_parent
from .character_store import CharacterStore
from .encounter_store import EncounterStore
from .merchant_store import MerchantStore
from .merchants import MerchantRecord, MerchantStockLine, MerchantTag, MerchantTransaction, PriceRule, bundle_as_merchant, eligible_items, final_price, generate_stock, haggle, merchant_document, now_iso, price_breakdown, reconciliation_lines
from .diagnostics import configure_logging, log_dir
from .encounters import (
    DAMAGE_TYPE_RULES,
    DIFFICULTY_MULTIPLIERS,
    CUSTOM_CONDITIONS,
    STANDARD_CONDITIONS,
    ActionCard,
    Campaign,
    Combatant,
    ConditionInstance,
    EncounterBuilder,
    EncounterController,
    EncounterSession,
    EncounterTemplate,
    EnvironmentModifier,
    LootEntry,
    Objective,
    RecipeSlot,
    ResourceCounter,
    RulesEngine,
    action_document,
    combatant_document,
    condition_document,
    initiative_document,
    loot_document,
    reveal_loot_for,
    stage_loot,
    summary_document,
    target_threat,
    threat_score,
)
from .characters import (
    ABILITY_KEYS,
    DEFENSE_KEYS,
    ROLE_WEIGHTS,
    SECTIONS,
    Character,
    CharacterGenerator,
    GeneratorSettings,
    character_text,
    essential_item_text,
    format_speed,
    format_defenses,
    toughness_token_spans,
)
from .config import CATEGORY_GIDS, SYNC_INTERVAL_SECONDS, UserSettings, app_data_dir, load_user_settings, save_user_settings
from .models import CatalogSnapshot, GearItem, JobStatus, PrintDocument
from .printer import BluetoothPrinter, normalize_address
from .rendering import tag_text
from .spooler import PrintSpooler
from .updater import RELEASES_PAGE_URL, ReleaseInfo, fetch_latest_release, is_newer
from .ui_widgets import FormField, choose_records, prompt_form
from .encounter_workflow import weapon_ammo, change_weapon_ammo
from .encounter_panels import encounter_panel_content, feature_label


LOGGER = logging.getLogger("ctp500.ui")


MERCHANT_TAG_PRESETS: tuple[tuple[str, str], ...] = (
    ("Arms dealer", "Weapons"),
    ("Armorer", "Apparel"),
    ("Cybernetics clinic", "Bionics"),
    ("Gene treatment lab", "Gene Treatments"),
    ("General store", "Consumables/Gear"),
    ("Vehicle dealer", "Vehicles"),
)
TOUGHNESS_COLORS = {"base": "#000000", "armor": "#d97706", "shield": "#1769aa", "both": "#2e7d32"}


GENERAL_COLUMNS = (
    "Category",
    "Name",
    "Manufacturer",
    "Kind",
    "Rank",
    "Special Notes",
    "Price",
    "Corruption",
    "Capacity",
)

GENERAL_COLUMN_WIDTHS = {
    "Category": 130,
    "Name": 250,
    "Manufacturer": 180,
    "Kind": 130,
    "Rank": 65,
    "Special Notes": 180,
    "Price": 90,
    "Corruption": 90,
    "Capacity": 85,
}

ENCOUNTER_HOTKEYS = (
    ("Space", "Next turn"),
    ("I", "Roll initiative"),
    ("S", "Set selected initiative"),
    ("Enter", "Resolve selected action"),
    ("B / Shift+B", "Add / remove a bruise"),
    ("C", "Add condition"),
    ("D", "Toggle defeated"),
    ("R / H / K", "Ready / hold / skip"),
    ("1 / 2 / 3", "Use standard / move / reaction"),
    ("L", "Reveal selected loot"),
    ("O", "Advance selected objective"),
    ("F6 / F7", "Focus roster / target"),
    ("Escape", "Clear action preview"),
    ("Ctrl+Z / Ctrl+Y", "Undo / redo"),
    ("F1", "Show encounter hotkeys"),
)


def catalog_columns(items: tuple[GearItem, ...], category: str) -> tuple[str, ...]:
    """Return normalized columns for All, or source-sheet columns for a category."""
    if category == "All":
        return GENERAL_COLUMNS
    item = next((item for item in items if item.category == category), None)
    return tuple(label for label, _value in item.fields) if item else GENERAL_COLUMNS


def catalog_value(item: GearItem, column: str, category: str) -> str:
    if category != "All":
        return item.field_map.get(column, "")
    values = {
        "Category": item.category,
        "Name": item.name,
        "Manufacturer": item.manufacturer,
        "Kind": item.kind,
        "Rank": item.rank,
        "Special Notes": item.field_map.get("Special Notes", ""),
        "Price": item.price,
        "Corruption": item.field_map.get("Corruption", ""),
        "Capacity": item.field_map.get("Capacity", item.field_map.get("Cap", "")),
    }
    return values.get(column, "")


def stretch_catalog_column(column: str, columns: tuple[str, ...]) -> bool:
    """Keep spare table width in the rightmost column only."""
    return bool(columns) and column == columns[-1]


class GearPrinterApp:
    def __init__(self, root: tk.Tk) -> None:
        configure_logging()
        self.root = root
        self.root.report_callback_exception = self._report_callback_exception
        self.root.title(f"CTP500 Live Gear and Minion Printer v{__version__}")
        self.root.geometry("1240x760")
        self.root.minsize(980, 620)

        self.client = CatalogClient()
        self.printer = BluetoothPrinter()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.spooler = PrintSpooler(self.printer, listener=lambda: self.events.put(("jobs", None)))
        self.character_generator = CharacterGenerator()
        self._temporary_storage: tempfile.TemporaryDirectory[str] | None = None
        self._restored_settings_pending_restart = False
        self.storage_error = ""
        try:
            self.character_store = CharacterStore()
            self.encounter_store = EncounterStore(self.character_store.path)
            self.merchant_store = MerchantStore(self.character_store.path)
        except Exception as exc:
            LOGGER.exception("Could not open application database")
            self.storage_error = str(exc)
            self._temporary_storage = tempfile.TemporaryDirectory(prefix="ctp500-recovery-")
            fallback = Path(self._temporary_storage.name) / "characters.db"
            self.character_store = CharacterStore(fallback)
            self.encounter_store = EncounterStore(fallback)
            self.merchant_store = MerchantStore(fallback)
        self.encounter_builder = EncounterBuilder(self.character_generator)
        self.current_encounter: EncounterSession | None = None
        self.encounter_controller: EncounterController | None = None
        self.current_character: Character | None = None
        self.current_merchant: MerchantRecord | None = None
        self.expanded_character_items: set[str] = set()
        self.essential_character_items: set[str] = set()
        self.character_physical_profile_expanded = False
        self.snapshot: CatalogSnapshot | None = load_catalog_cache()
        self._snapshot_is_cached = self.snapshot is not None
        self.visible_items: list[GearItem] = []
        self.item_by_iid: dict[str, GearItem] = {}
        self.job_by_iid: dict[str, str] = {}
        self.char_job_by_iid: dict[str, str] = {}
        self.sort_column = "Category"
        self.sort_reverse = False
        self._closing = False
        self._sync_stop = threading.Event()
        self._sync_trigger = threading.Event()

        settings = load_user_settings()
        self.dataset_url_var = tk.StringVar(value=settings.dataset_url)
        try:
            self.client = CatalogClient(dataset_url=settings.dataset_url)
        except CatalogError:
            self.client = CatalogClient()
        self.column_widths = {
            view: dict(widths) for view, widths in settings.column_widths.items()
        }
        self.initiative_presets = list(settings.initiative_presets)
        self.saved_character_settings = dict(settings.character_settings)
        self.saved_encounter_generation_settings = dict(settings.encounter_generation_settings)
        saved_enabled = self.saved_character_settings.get("race_enabled", {})
        saved_weights = self.saved_character_settings.get("race_weights", {})
        saved_parent_enabled = self.saved_character_settings.get("parent_enabled", {})
        saved_parent_weights = self.saved_character_settings.get("parent_weights", {})
        self.race_enabled = {race.name: bool(saved_enabled.get(race.name, True)) for race in RACES} if isinstance(saved_enabled, dict) else {race.name: True for race in RACES}
        self.race_weights = {race.name: float(saved_weights.get(race.name, race.default_weight)) for race in RACES} if isinstance(saved_weights, dict) else {race.name: race.default_weight for race in RACES}
        self.parent_enabled = {parent: bool(saved_parent_enabled.get(parent, True)) for parent in RACE_GROUPS} if isinstance(saved_parent_enabled, dict) else {parent: True for parent in RACE_GROUPS}
        self.parent_weights = {parent: float(saved_parent_weights.get(parent, 1.0)) for parent in RACE_GROUPS} if isinstance(saved_parent_weights, dict) else {parent: 1.0 for parent in RACE_GROUPS}
        self.search_var = tk.StringVar()
        self.category_var = tk.StringVar(value="All")
        self.manufacturer_var = tk.StringVar(value="All")
        self.kind_var = tk.StringVar(value="All")
        self.rank_var = tk.StringVar(value="All")
        self.quantity_var = tk.IntVar(value=1)
        self.address_var = tk.StringVar(value=settings.bluetooth_address)
        self.sync_status_var = tk.StringVar(
            value=self._cached_catalog_status() if self.snapshot else "Catalog: unavailable — waiting for refresh"
        )
        self.printer_status_var = tk.StringVar(value="Printer: disconnected")
        self.item_count_var = tk.StringVar(value="0 items")

        self.char_name_var = tk.StringVar()
        self.char_pl_var = tk.IntVar(value=int(self.saved_character_settings.get("power_level", 5)))
        self.char_role_var = tk.StringVar(value=str(self.saved_character_settings.get("role", "Generalist")))
        self.char_race_var = tk.StringVar(value="Random")
        self.char_budget_var = tk.DoubleVar(value=float(self.saved_character_settings.get("budget", 10000)))
        self.char_weapon_var = tk.StringVar(value=str(self.saved_character_settings.get("weapon_mode", "Auto")))
        self.char_guarantee_weapon_var = tk.BooleanVar(value=bool(self.saved_character_settings.get("guarantee_weapon", False)))
        self.char_quality_var = tk.DoubleVar(value=float(self.saved_character_settings.get("quality", 0)))
        self.char_quantity_var = tk.DoubleVar(value=float(self.saved_character_settings.get("quantity", 0)))
        self.char_quality_enabled_var = tk.BooleanVar(value=bool(self.saved_character_settings.get("quality_enabled", True)))
        self.char_quantity_enabled_var = tk.BooleanVar(value=bool(self.saved_character_settings.get("quantity_enabled", True)))
        self.char_catalog_ignore_limits_var = tk.BooleanVar(value=bool(self.saved_character_settings.get("catalog_adds_ignore_limits", False)))
        self.char_affinity_var = tk.DoubleVar(value=float(self.saved_character_settings.get("affinity_strength", 1)))
        self.encounter_guarantee_weapon_var = tk.BooleanVar(value=bool(self.saved_character_settings.get("guarantee_weapon", False)))
        self.char_seed_var = tk.StringVar()
        self.char_search_var = tk.StringVar()
        self.char_status_var = tk.StringVar(value="Generate a character after the catalog loads.")
        self.merchant_name_var = tk.StringVar()
        self.merchant_type_var = tk.StringVar(value="Merchant")
        self.merchant_wealth_var = tk.IntVar(value=2)
        self.merchant_seed_var = tk.StringVar()
        self.merchant_specialties_var = tk.StringVar()
        self.merchant_affiliations_var = tk.StringVar()
        self.merchant_status_var = tk.StringVar(value="Create a merchant or stock bundle.")
        self.ability_vars = {key: tk.IntVar(value=0) for key in ABILITY_KEYS}
        self.defense_vars = {key: tk.IntVar(value=0) for key in DEFENSE_KEYS}
        self.category_initial_vars: dict[str, tk.DoubleVar] = {}
        self.category_continue_vars: dict[str, tk.DoubleVar] = {}
        self.category_max_vars: dict[str, tk.IntVar] = {}

        self._build_ui()
        if self.snapshot:
            self._update_filter_values()
            self._refresh_catalog_view()
        # Use an application-level binding so the shortcut is independent of
        # whichever preview/tree control currently owns keyboard focus.
        self.root.bind_all("<KeyPress-x>", self._expand_orange_hotkey, add="+")
        self.root.bind_all("<KeyPress-a>", self._all_orange_hotkey, add="+")
        self.root.bind_all("<KeyPress>", self._encounter_hotkey, add="+")
        self._start_sync()
        self.root.after(1500, lambda: self._check_for_updates(automatic=True))
        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.storage_error:
            self.root.after(0, self._show_storage_recovery_warning)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.configure("Treeview", rowheight=25)

        connection = ttk.Frame(self.root, padding=(10, 8))
        connection.pack(fill="x")
        ttk.Label(connection, text="Bluetooth address:").pack(side="left")
        self.address_entry = ttk.Entry(connection, textvariable=self.address_var, width=20)
        self.address_entry.pack(side="left", padx=(6, 6))
        self.connect_button = ttk.Button(connection, text="Connect", command=self._connect)
        self.connect_button.pack(side="left")
        self.disconnect_button = ttk.Button(connection, text="Disconnect", command=self._disconnect)
        self.disconnect_button.pack(side="left", padx=(6, 12))
        ttk.Label(connection, textvariable=self.printer_status_var).pack(side="left")
        ttk.Button(connection, text="Refresh catalog", command=self._request_refresh).pack(side="right")
        self.update_button = ttk.Button(connection, text="Check for updates", command=self._check_for_updates)
        self.update_button.pack(side="right", padx=(6, 0))
        ttk.Button(connection, text="Data Management", command=self._open_data_management).pack(side="right", padx=(6, 0))
        ttk.Label(connection, textvariable=self.sync_status_var).pack(side="right", padx=12)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.gear_tab = ttk.Frame(self.notebook)
        self.character_tab = ttk.Frame(self.notebook)
        self.merchant_tab = ttk.Frame(self.notebook)
        self.encounter_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.gear_tab, text="Gear Catalog")
        self.notebook.add(self.character_tab, text="Character Creator")
        self.notebook.add(self.merchant_tab, text="Merchants")
        self.notebook.add(self.encounter_tab, text="Encounter Tracker")

        filters = ttk.LabelFrame(self.gear_tab, text="Search and filters", padding=8)
        filters.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(filters, text="Search:").grid(row=0, column=0, sticky="w")
        search = ttk.Entry(filters, textvariable=self.search_var)
        search.grid(row=0, column=1, sticky="ew", padx=(5, 10))
        search.bind("<KeyRelease>", lambda _event: self._refresh_catalog_view())

        self.filter_boxes: dict[str, ttk.Combobox] = {}
        for column, (label, variable) in enumerate(
            (
                ("Category", self.category_var),
                ("Manufacturer", self.manufacturer_var),
                ("Kind", self.kind_var),
                ("Rank", self.rank_var),
            ),
            start=2,
        ):
            ttk.Label(filters, text=f"{label}:").grid(row=0, column=column * 2 - 2, sticky="e")
            box = ttk.Combobox(filters, textvariable=variable, state="readonly", width=18)
            box.grid(row=0, column=column * 2 - 1, padx=(5, 10), sticky="ew")
            box["values"] = ("All",)
            box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_catalog_view())
            self.filter_boxes[label] = box
        ttk.Button(filters, text="Clear", command=self._clear_filters).grid(row=0, column=10)
        filters.columnconfigure(1, weight=2)
        filters.columnconfigure(5, weight=1)

        main = ttk.Panedwindow(self.gear_tab, orient="horizontal")
        main.pack(fill="both", expand=True, pady=(0, 8))

        catalog_panel = ttk.Panedwindow(main, orient="vertical")
        main.add(catalog_panel, weight=4)

        table_frame = ttk.Frame(catalog_panel)
        catalog_panel.add(table_frame, weight=4)
        self.catalog_tree = ttk.Treeview(
            table_frame,
            columns=GENERAL_COLUMNS,
            show="headings",
            selectmode="extended",
        )
        self._configure_catalog_columns(GENERAL_COLUMNS, "All")
        table_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.catalog_tree.yview)
        table_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.catalog_tree.xview)
        self.catalog_tree.configure(yscrollcommand=table_y.set, xscrollcommand=table_x.set)
        self.catalog_tree.grid(row=0, column=0, sticky="nsew")
        table_y.grid(row=0, column=1, sticky="ns")
        table_x.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.catalog_tree.bind("<<TreeviewSelect>>", self._show_details)
        self.catalog_tree.bind("<Double-1>", self._quick_print)
        self.catalog_tree.bind("<ButtonRelease-1>", self._column_resize_finished, add="+")

        details_frame = ttk.LabelFrame(catalog_panel, text="Source details and printed tag", padding=5)
        catalog_panel.add(details_frame, weight=2)
        self.details_text = tk.Text(details_frame, height=10, wrap="word", state="disabled")
        details_y = ttk.Scrollbar(details_frame, command=self.details_text.yview)
        self.details_text.configure(yscrollcommand=details_y.set)
        self.details_text.pack(side="left", fill="both", expand=True)
        details_y.pack(side="right", fill="y")

        spool_frame = ttk.LabelFrame(main, text="Print spooler", padding=8)
        main.add(spool_frame, weight=2)
        spool_controls = ttk.Frame(spool_frame)
        spool_controls.pack(fill="x", pady=(0, 6))
        ttk.Label(spool_controls, text="Copies:").pack(side="left")
        ttk.Spinbox(spool_controls, from_=1, to=99, textvariable=self.quantity_var, width=4).pack(side="left", padx=5)
        ttk.Button(spool_controls, text="Queue selected", command=self._queue_selected).pack(side="left", fill="x", expand=True)

        spool_columns = ("Item", "Category", "Copies", "Status")
        self.spool_tree = ttk.Treeview(spool_frame, columns=spool_columns, show="headings", selectmode="browse")
        for column, width in (("Item", 180), ("Category", 105), ("Copies", 65), ("Status", 85)):
            self.spool_tree.heading(column, text=column)
            self.spool_tree.column(column, width=width, minwidth=55, stretch=column == "Item")
        spool_y = ttk.Scrollbar(spool_frame, orient="vertical", command=self.spool_tree.yview)
        self.spool_tree.configure(yscrollcommand=spool_y.set)
        self.spool_tree.pack(side="top", fill="both", expand=True)
        spool_y.place(relx=1.0, rely=0.08, relheight=0.75, anchor="ne")
        self.spool_tree.bind("<<TreeviewSelect>>", self._show_job_error)

        actions = ttk.Frame(spool_frame)
        actions.pack(fill="x", pady=(6, 0))
        ttk.Button(actions, text="Cancel", command=self._cancel_job).pack(side="left")
        ttk.Button(actions, text="Retry", command=self._retry_job).pack(side="left", padx=4)
        ttk.Button(actions, text="Remove", command=self._remove_job).pack(side="left")
        ttk.Button(actions, text="Clear finished", command=self.spooler.clear_finished).pack(side="right")
        self.job_error_var = tk.StringVar()
        ttk.Label(spool_frame, textvariable=self.job_error_var, foreground="#9c1c1c", wraplength=330).pack(fill="x", pady=(6, 0))

        footer = ttk.Frame(self.gear_tab, padding=(0, 0, 0, 4))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.item_count_var).pack(side="left")
        ttk.Label(footer, text="Double-click an item to queue one priority tag.").pack(side="right")

        self._build_character_ui(self.character_tab)
        self._build_merchant_ui(self.merchant_tab)
        self._build_encounter_ui(self.encounter_tab)

    def _build_character_ui(self, parent: ttk.Frame) -> None:
        controls = ttk.LabelFrame(parent, text="Generator", padding=8)
        controls.pack(fill="x", padx=2, pady=(2, 6))
        fields = (
            ("Name", ttk.Entry(controls, textvariable=self.char_name_var, width=18)),
            ("PL", ttk.Spinbox(controls, from_=1, to=20, textvariable=self.char_pl_var, width=4)),
            ("Role", ttk.Combobox(controls, textvariable=self.char_role_var, values=("Random", *ROLE_WEIGHTS), state="readonly", width=12)),
            ("Race", ttk.Combobox(controls, textvariable=self.char_race_var, values=("Random", *[race.name for race in RACES if self.parent_enabled.get(race_parent(race.name), True) and self.parent_weights.get(race_parent(race.name), 1) > 0 and self.race_enabled.get(race.name, True) and self.race_weights.get(race.name, 1) > 0]), state="readonly", width=24)),
            ("Budget", ttk.Entry(controls, textvariable=self.char_budget_var, width=10)),
            ("Weapons", ttk.Combobox(controls, textvariable=self.char_weapon_var, values=("Auto", "Mixed", "Melee", "Ranged", "None"), state="readonly", width=9)),
            ("Seed", ttk.Entry(controls, textvariable=self.char_seed_var, width=12)),
        )
        for column, (label, widget) in enumerate(fields):
            ttk.Label(controls, text=label).grid(row=0, column=column, sticky="w", padx=(0, 4))
            widget.grid(row=1, column=column, sticky="ew", padx=(0, 8))
            if label == "Race":
                self.race_box = widget
        ttk.Checkbutton(controls, text="Quality", variable=self.char_quality_enabled_var, command=self._update_qq_controls).grid(row=0, column=7)
        self.char_quality_scale = ttk.Scale(controls, from_=-1, to=1, variable=self.char_quality_var)
        self.char_quality_scale.grid(row=1, column=7, sticky="ew")
        ttk.Checkbutton(controls, text="Quantity", variable=self.char_quantity_enabled_var, command=self._update_qq_controls).grid(row=0, column=8)
        self.char_quantity_scale = ttk.Scale(controls, from_=-1, to=1, variable=self.char_quantity_var)
        self.char_quantity_scale.grid(row=1, column=8, sticky="ew")
        ttk.Label(controls, text="Brand affinity").grid(row=0, column=9)
        ttk.Scale(controls, from_=0, to=5, variable=self.char_affinity_var).grid(row=1, column=9, sticky="ew")
        ttk.Checkbutton(controls, text="Guarantee weapon", variable=self.char_guarantee_weapon_var).grid(row=0, column=10, sticky="w")
        ttk.Button(controls, text="Race chances…", command=self._open_race_editor).grid(row=1, column=10, padx=5)
        ttk.Button(controls, text="Generate", command=self._generate_character).grid(row=1, column=11, padx=5)
        ttk.Button(controls, text="New seed", command=self._generate_new_seed).grid(row=1, column=12, padx=2)
        ttk.Scale(controls, from_=0, to=1_000_000, variable=self.char_budget_var).grid(row=2, column=0, columnspan=13, sticky="ew", pady=(5, 0))
        budget_references = ttk.Frame(controls)
        budget_references.grid(row=3, column=0, columnspan=13)
        ttk.Label(budget_references, text="Budget strata:").pack(side="left")
        levels = (
            ("Street", 100),
            ("Working", 1_000),
            ("Professional", 10_000),
            ("Affluent", 100_000),
            ("Elite", 1_000_000),
        )
        for index, (label, amount) in enumerate(levels):
            if index:
                ttk.Label(budget_references, text=" • ").pack(side="left")
            reference = tk.Label(
                budget_references,
                text=f"{label} {amount:,}",
                foreground="#1769aa",
                cursor="hand2",
                font=("TkDefaultFont", 9, "underline"),
            )
            reference.pack(side="left")
            reference.bind("<Button-1>", lambda _event, value=amount: self._set_character_budget(value))
        for column in (0, 3, 7, 8, 9):
            controls.columnconfigure(column, weight=1)
        self._update_qq_controls()

        self.char_advanced_visible = False
        ttk.Button(parent, text="Advanced generator settings ▾", command=self._toggle_character_advanced).pack(anchor="w", padx=4, pady=(0, 5))
        self.char_advanced_frame = ttk.Frame(parent)
        quick = ttk.LabelFrame(self.char_advanced_frame, text="Quick probabilities (initial / continue / max)", padding=5)
        quick.pack(fill="x", padx=2, pady=(0, 6))
        defaults = GeneratorSettings()
        for column, category in enumerate(("Weapons", "Armor", "Bionics", "Genemods", "Inventory")):
            initial_saved = self.saved_character_settings.get("category_initial", {})
            continue_saved = self.saved_character_settings.get("category_continue", {})
            max_saved = self.saved_character_settings.get("category_max", {})
            initial = float(initial_saved.get(category, defaults.category_initial[category])) if isinstance(initial_saved, dict) else defaults.category_initial[category]
            continuation = float(continue_saved.get(category, defaults.category_continue[category])) if isinstance(continue_saved, dict) else defaults.category_continue[category]
            maximum = int(max_saved.get(category, defaults.category_max[category])) if isinstance(max_saved, dict) else defaults.category_max[category]
            self.category_initial_vars[category] = tk.DoubleVar(value=initial)
            self.category_continue_vars[category] = tk.DoubleVar(value=continuation)
            self.category_max_vars[category] = tk.IntVar(value=maximum)
            ttk.Label(quick, text=category).grid(row=0, column=column)
            row = ttk.Frame(quick)
            row.grid(row=1, column=column, padx=4)
            ttk.Entry(row, textvariable=self.category_initial_vars[category], width=5).pack(side="left")
            ttk.Entry(row, textvariable=self.category_continue_vars[category], width=5).pack(side="left", padx=2)
            ttk.Entry(row, textvariable=self.category_max_vars[category], width=4).pack(side="left")

        stat_editor = ttk.LabelFrame(self.char_advanced_frame, text="Editable base abilities / defense training", padding=4)
        stat_editor.pack(fill="x", padx=2, pady=(0, 6))
        for column, key in enumerate(ABILITY_KEYS):
            ttk.Label(stat_editor, text=key).grid(row=0, column=column)
            ttk.Spinbox(stat_editor, from_=-20, to=99, textvariable=self.ability_vars[key], width=4).grid(row=1, column=column, padx=2)
        for offset, key in enumerate(DEFENSE_KEYS, start=len(ABILITY_KEYS)):
            ttk.Label(stat_editor, text=f"{key}+").grid(row=0, column=offset)
            ttk.Spinbox(stat_editor, from_=0, to=99, textvariable=self.defense_vars[key], width=4).grid(row=1, column=offset, padx=2)

        panes = ttk.Panedwindow(parent, orient="horizontal")
        self.character_panes = panes
        panes.pack(fill="both", expand=True, padx=2)
        editor = ttk.Panedwindow(panes, orient="vertical")
        panes.add(editor, weight=3)
        preview_frame = ttk.LabelFrame(editor, text="Live statblock", padding=5)
        editor.add(preview_frame, weight=3)
        self.character_preview = tk.Text(preview_frame, wrap="word", state="disabled")
        preview_y = ttk.Scrollbar(preview_frame, command=self.character_preview.yview)
        self.character_preview.configure(yscrollcommand=preview_y.set)
        self.character_preview.pack(side="left", fill="both", expand=True)
        preview_y.pack(side="right", fill="y")

        equipment_frame = ttk.LabelFrame(editor, text="Equipment", padding=5)
        editor.add(equipment_frame, weight=2)
        columns = ("Section", "Item", "Manufacturer", "Cost", "Locked")
        item_actions = ttk.Frame(equipment_frame)
        item_actions.pack(fill="x", pady=(0, 4))
        ttk.Button(item_actions, text="Browse gear…", command=lambda: self._add_catalog_item(self._choose_catalog_items("Add gear to character"))).pack(side="left")
        ttk.Checkbutton(item_actions, text="Ignore add limits", variable=self.char_catalog_ignore_limits_var).pack(side="left", padx=4)
        ttk.Button(item_actions, text="Remove", command=self._remove_character_item).pack(side="left", padx=3)
        ttk.Button(item_actions, text="Lock/Unlock", command=self._toggle_character_item_lock).pack(side="left")
        ttk.Button(item_actions, text="Reroll item", command=self._reroll_character_item).pack(side="left", padx=3)
        ttk.Button(item_actions, text="Reroll section", command=self._reroll_character_section).pack(side="left")
        item_actions_secondary = ttk.Frame(equipment_frame)
        item_actions_secondary.pack(fill="x", pady=(0, 4))
        ttk.Button(item_actions_secondary, text="Toggle all details", command=self._toggle_all_character_items).pack(side="left")
        ttk.Button(item_actions_secondary, text="Toggle brief (x)", command=self._toggle_orange_character_items).pack(side="left", padx=3)
        ttk.Button(item_actions_secondary, text="All brief (a)", command=self._make_all_character_items_orange).pack(side="left")
        ttk.Button(item_actions_secondary, text="Print selected item(s)", command=self._print_selected_character_items).pack(side="left", padx=3)
        ttk.Button(item_actions_secondary, text="Recalculate", command=self._recalculate_character).pack(side="right")
        self.character_equipment_tree = ttk.Treeview(equipment_frame, columns=columns, show="headings", selectmode="extended", height=7)
        for column, width in (("Section", 90), ("Item", 220), ("Manufacturer", 150), ("Cost", 70), ("Locked", 55)):
            self.character_equipment_tree.heading(column, text=column)
            self.character_equipment_tree.column(column, width=width, stretch=column == "Item")
        self.character_equipment_tree.pack(fill="both", expand=True)
        self.character_equipment_tree.bind("<Double-1>", lambda _event: self._toggle_selected_character_item_details())

        saved = ttk.LabelFrame(panes, text="Saved characters", padding=6)
        panes.add(saved, weight=2)
        search = ttk.Entry(saved, textvariable=self.char_search_var)
        search.pack(fill="x", pady=(0, 4))
        search.bind("<KeyRelease>", lambda _event: self._refresh_saved_characters())
        columns = ("Name", "Race", "Role", "PL")
        self.saved_character_tree = ttk.Treeview(saved, columns=columns, show="headings", selectmode="browse")
        for column, width in (("Name", 120), ("Race", 160), ("Role", 85), ("PL", 35)):
            self.saved_character_tree.heading(column, text=column)
            self.saved_character_tree.column(column, width=width, stretch=column in {"Name", "Race"})
        self.saved_character_tree.pack(fill="both", expand=True)
        self.saved_character_tree.bind("<Double-1>", lambda _event: self._load_saved_character())
        saved_actions = ttk.Frame(saved)
        saved_actions.pack(fill="x", pady=(5, 0))
        ttk.Button(saved_actions, text="Load", command=self._load_saved_character).pack(side="left")
        ttk.Button(saved_actions, text="Save", command=self._save_character).pack(side="left", padx=3)
        ttk.Button(saved_actions, text="Duplicate", command=self._duplicate_character).pack(side="left")
        ttk.Button(saved_actions, text="Delete", command=self._delete_character).pack(side="left", padx=3)
        ttk.Button(saved_actions, text="Print", command=self._print_character).pack(side="right")

        character_queue = ttk.LabelFrame(panes, text="Print queue", padding=6)
        panes.add(character_queue, weight=2)
        queue_columns = ("Item", "Category", "Copies", "Status")
        self.char_spool_tree = ttk.Treeview(character_queue, columns=queue_columns, show="headings", selectmode="browse")
        for column, width in (("Item", 150), ("Category", 85), ("Copies", 58), ("Status", 78)):
            self.char_spool_tree.heading(column, text=column)
            self.char_spool_tree.column(column, width=width, minwidth=45, stretch=column == "Item")
        char_spool_y = ttk.Scrollbar(character_queue, orient="vertical", command=self.char_spool_tree.yview)
        self.char_spool_tree.configure(yscrollcommand=char_spool_y.set)
        self.char_spool_tree.pack(side="top", fill="both", expand=True)
        char_spool_y.place(relx=1.0, rely=0.0, relheight=0.75, anchor="ne")
        self.char_spool_tree.bind("<<TreeviewSelect>>", self._show_char_job_error)
        queue_actions = ttk.Frame(character_queue)
        queue_actions.pack(fill="x", pady=(5, 0))
        ttk.Button(queue_actions, text="Cancel", command=lambda: self._char_job_action("cancel")).pack(side="left")
        ttk.Button(queue_actions, text="Retry", command=lambda: self._char_job_action("retry")).pack(side="left", padx=3)
        ttk.Button(queue_actions, text="Remove", command=lambda: self._char_job_action("remove")).pack(side="left")
        ttk.Button(queue_actions, text="Clear finished", command=self.spooler.clear_finished).pack(side="right")
        self.char_job_error_var = tk.StringVar()
        ttk.Label(character_queue, textvariable=self.char_job_error_var, foreground="#9c1c1c", wraplength=260).pack(fill="x", pady=(5, 0))
        ttk.Label(parent, textvariable=self.char_status_var).pack(fill="x", padx=4, pady=3)
        self._refresh_saved_characters()
        self._refresh_jobs()

    def _build_merchant_ui(self, parent: ttk.Frame) -> None:
        """Merchant workspace deliberately mirrors the character editor's saved/live split."""
        controls = ttk.LabelFrame(parent, text="Merchant / stock bundle", padding=7)
        controls.pack(fill="x", padx=4, pady=4)
        fields = (("Name", self.merchant_name_var, 22), ("Type", self.merchant_type_var, 12),
                  ("Wealth", self.merchant_wealth_var, 5), ("Seed", self.merchant_seed_var, 15))
        for column, (label, variable, width) in enumerate(fields):
            ttk.Label(controls, text=label).grid(row=0, column=column, sticky="w")
            if label == "Type":
                widget = ttk.Combobox(controls, textvariable=variable, values=("Merchant", "Bundle"), state="readonly", width=width)
            elif label == "Wealth":
                widget = ttk.Spinbox(controls, from_=0, to=4, textvariable=variable, width=width)
            else:
                widget = ttk.Entry(controls, textvariable=variable, width=width)
            widget.grid(row=1, column=column, padx=(0, 5), sticky="ew")
        ttk.Button(controls, text="New", command=self._new_merchant).grid(row=1, column=6, padx=2)
        ttk.Button(controls, text="Generate", command=self._generate_merchant_stock).grid(row=1, column=7, padx=2)
        ttk.Button(controls, text="Save", command=self._save_merchant).grid(row=1, column=8, padx=2)
        ttk.Button(controls, text="Print", command=self._print_merchant).grid(row=1, column=9, padx=2)
        ttk.Button(controls, text="Tags…", command=self._merchant_tags).grid(row=1, column=10, padx=2)
        ttk.Button(controls, text="Price rules…", command=self._merchant_price_rules).grid(row=1, column=11, padx=2)
        ttk.Button(controls, text="Haggle…", command=self._merchant_haggle_rules).grid(row=1, column=12, padx=2)
        ttk.Button(controls, text="Ledger…", command=self._merchant_ledger).grid(row=1, column=13, padx=2)

        selectors = ttk.Frame(controls)
        selectors.grid(row=2, column=0, columnspan=14, sticky="ew", pady=(6, 0))
        ttk.Label(selectors, text="Specialties:").pack(side="left")
        self.merchant_specialty_search_var = tk.StringVar()
        specialty_search = ttk.Entry(selectors, textvariable=self.merchant_specialty_search_var, width=13)
        specialty_search.pack(side="left", padx=(4, 0)); specialty_search.bind("<KeyRelease>", lambda _event: self._filter_merchant_selector("specialty"))
        self.merchant_specialty_list = tk.Listbox(selectors, height=3, selectmode="extended", exportselection=False, width=25)
        self.merchant_specialty_list.pack(side="left", padx=(4, 12))
        ttk.Label(selectors, text="Affiliations:").pack(side="left")
        self.merchant_affiliation_search_var = tk.StringVar()
        affiliation_search = ttk.Entry(selectors, textvariable=self.merchant_affiliation_search_var, width=13)
        affiliation_search.pack(side="left", padx=(4, 0)); affiliation_search.bind("<KeyRelease>", lambda _event: self._filter_merchant_selector("affiliation"))
        self.merchant_affiliation_list = tk.Listbox(selectors, height=3, selectmode="extended", exportselection=False, width=25)
        self.merchant_affiliation_list.pack(side="left", padx=4)
        ttk.Button(selectors, text="Add custom…", command=self._merchant_add_custom_selector).pack(side="left", padx=4)
        self.merchant_tag_chips = ttk.Frame(controls)
        self.merchant_tag_chips.grid(row=3, column=0, columnspan=14, sticky="ew", pady=(5, 0))

        panes = ttk.Panedwindow(parent, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        library = ttk.LabelFrame(panes, text="Saved merchants and bundles", padding=5)
        panes.add(library, weight=2)
        self.merchant_search_var = tk.StringVar()
        search = ttk.Entry(library, textvariable=self.merchant_search_var)
        search.pack(fill="x"); search.bind("<KeyRelease>", lambda _event: self._refresh_merchant_library())
        self.merchant_tree = ttk.Treeview(library, columns=("Name", "Type", "Wealth", "Stock"), show="headings", selectmode="browse")
        for col, width in (("Name", 190), ("Type", 75), ("Wealth", 55), ("Stock", 45)):
            self.merchant_tree.heading(col, text=col); self.merchant_tree.column(col, width=width)
        self.merchant_tree.pack(fill="both", expand=True, pady=4)
        self.merchant_tree.bind("<Double-1>", lambda _event: self._load_merchant())
        buttons = ttk.Frame(library); buttons.pack(fill="x")
        ttk.Button(buttons, text="Load", command=self._load_merchant).pack(side="left")
        ttk.Button(buttons, text="Duplicate", command=self._duplicate_merchant).pack(side="left", padx=3)
        ttk.Button(buttons, text="Delete", command=self._delete_merchant).pack(side="left")

        stock_frame = ttk.LabelFrame(panes, text="Stock by category", padding=5)
        panes.add(stock_frame, weight=5)
        stock_filter = ttk.Frame(stock_frame); stock_filter.pack(fill="x")
        ttk.Label(stock_filter, text="Stock filter:").pack(side="left")
        self.merchant_stock_filter_var = tk.StringVar(value="All")
        self.merchant_stock_filter_box = ttk.Combobox(stock_filter, textvariable=self.merchant_stock_filter_var, state="normal", width=18)
        self.merchant_stock_filter_box.pack(side="left", padx=4); self.merchant_stock_filter_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_merchant_stock())
        self.merchant_stock_filter_box.bind("<KeyRelease>", lambda _event: self._refresh_merchant_stock())
        ttk.Label(stock_filter, text="Search stock:").pack(side="left", padx=(10, 0))
        self.merchant_stock_text_filter_var = tk.StringVar()
        stock_search = ttk.Entry(stock_filter, textvariable=self.merchant_stock_text_filter_var, width=22)
        stock_search.pack(side="left", padx=4); stock_search.bind("<KeyRelease>", lambda _event: self._refresh_merchant_stock())
        ttk.Button(stock_filter, text="Select category", command=self._merchant_select_category).pack(side="left")
        self.merchant_stock_canvas = tk.Canvas(stock_frame, highlightthickness=0)
        stock_scroll = ttk.Scrollbar(stock_frame, orient="vertical", command=self.merchant_stock_canvas.yview)
        self.merchant_stock_canvas.configure(yscrollcommand=stock_scroll.set)
        stock_scroll.pack(side="right", fill="y"); self.merchant_stock_canvas.pack(fill="both", expand=True)
        self.merchant_stock_panels = ttk.Frame(self.merchant_stock_canvas)
        self.merchant_stock_canvas.create_window((0, 0), window=self.merchant_stock_panels, anchor="nw")
        self.merchant_stock_panels.bind("<Configure>", lambda _event: self.merchant_stock_canvas.configure(scrollregion=self.merchant_stock_canvas.bbox("all")))
        self.merchant_stock_tree = None
        self.merchant_line_by_tree: dict[str, MerchantStockLine] = {}
        self.merchant_stock_sort_column = "Name"
        self.merchant_stock_sort_descending = False
        stock_buttons = ttk.Frame(stock_frame); stock_buttons.pack(fill="x", pady=4)
        for label, command in (("Browse gear…", lambda: self._merchant_add_catalog(self._choose_catalog_items("Add merchant stock"))), ("Quantity", self._merchant_quantity), ("Sold out", self._merchant_sold_out), ("Lock", self._merchant_lock), ("Item price", self._merchant_item_price), ("Remove", self._merchant_remove_line), ("Sell / Haggle", self._merchant_sell), ("To encounter loot", self._merchant_to_loot)):
            ttk.Button(stock_buttons, text=label, command=command).pack(side="left", padx=2)
        ttk.Button(stock_buttons, text="Bulk sold out", command=lambda: self._merchant_bulk_state(True)).pack(side="left", padx=2)
        ttk.Button(stock_buttons, text="Bulk available", command=lambda: self._merchant_bulk_state(False)).pack(side="left", padx=2)
        self.merchant_detail = tk.Text(stock_frame, height=7, wrap="word", state="disabled")
        self.merchant_detail.pack(fill="x")
        ttk.Label(parent, textvariable=self.merchant_status_var).pack(fill="x", padx=7, pady=(0, 4))
        self._refresh_merchant_library()
        self._refresh_merchant_selectors()

    @staticmethod
    def _split_merchant_tags(value: str) -> list[str]:
        return [part.strip() for part in value.split(",") if part.strip()]

    def _new_merchant(self) -> None:
        self.current_merchant = MerchantRecord(record_type=self.merchant_type_var.get(), name=self.merchant_name_var.get().strip() or "New Merchant", wealth_tier=self.merchant_wealth_var.get(), seed=self.merchant_seed_var.get().strip(), specialties=self._split_merchant_tags(self.merchant_specialties_var.get()), affiliations=self._split_merchant_tags(self.merchant_affiliations_var.get()))
        self._load_merchant_fields(); self.merchant_status_var.set("New record ready.")

    def _load_merchant_fields(self) -> None:
        record = self.current_merchant
        if not record: return
        self.merchant_name_var.set(record.name); self.merchant_type_var.set(record.record_type); self.merchant_wealth_var.set(record.wealth_tier); self.merchant_seed_var.set(record.seed)
        self.merchant_specialties_var.set(", ".join(record.specialties)); self.merchant_affiliations_var.set(", ".join(record.affiliations)); self._refresh_merchant_selectors(); self._restore_merchant_selector_values(); self._refresh_merchant_tag_chips(); self._refresh_merchant_stock()

    def _update_merchant_from_fields(self) -> MerchantRecord:
        if not self.current_merchant: self._new_merchant()
        assert self.current_merchant
        record = self.current_merchant; record.name = self.merchant_name_var.get().strip() or "Unnamed merchant"; record.record_type = self.merchant_type_var.get(); record.wealth_tier = self.merchant_wealth_var.get(); record.seed = self.merchant_seed_var.get().strip()
        record.specialties = self._merchant_selected_values(self.merchant_specialty_list) if hasattr(self, "merchant_specialty_list") else self._split_merchant_tags(self.merchant_specialties_var.get())
        record.affiliations = self._merchant_selected_values(self.merchant_affiliation_list) if hasattr(self, "merchant_affiliation_list") else self._split_merchant_tags(self.merchant_affiliations_var.get())
        record.updated_at = now_iso()
        return record

    def _merchant_selected_values(self, widget: tk.Listbox) -> list[str]:
        return [widget.get(index) for index in widget.curselection()]

    def _refresh_merchant_selectors(self) -> None:
        if not hasattr(self, "merchant_specialty_list"):
            return
        items = self.snapshot.items if self.snapshot else ()
        self.merchant_specialty_options = sorted({item.kind for item in items if item.kind} | {value for record in self.merchant_store.list() for value in record.specialties}, key=natural_key)
        self.merchant_affiliation_options = sorted({name for item in items for name in manufacturer_options((item,))} | {value for record in self.merchant_store.list() for value in record.affiliations}, key=natural_key)
        self._filter_merchant_selector("specialty")
        self._filter_merchant_selector("affiliation")
        categories = ["All", *sorted({item.category for item in items}, key=natural_key)]
        if hasattr(self, "merchant_stock_filter_box"):
            self.merchant_stock_filter_box["values"] = categories
            if self.merchant_stock_filter_var.get() not in categories: self.merchant_stock_filter_var.set("All")

    def _filter_merchant_selector(self, kind: str) -> None:
        """Live-filter a multi-select while retaining choices already selected."""
        if kind == "specialty":
            widget, options, query = self.merchant_specialty_list, getattr(self, "merchant_specialty_options", []), self.merchant_specialty_search_var.get()
        else:
            widget, options, query = self.merchant_affiliation_list, getattr(self, "merchant_affiliation_options", []), self.merchant_affiliation_search_var.get()
        selected = self._merchant_selected_values(widget)
        needle = query.strip().casefold()
        visible = [value for value in options if not needle or needle in value.casefold()]
        # A selected value stays visible even when it does not match the current search.
        visible = list(dict.fromkeys([*selected, *visible]))
        widget.delete(0, "end")
        for index, value in enumerate(visible):
            widget.insert("end", value)
            if value in selected: widget.selection_set(index)

    def _restore_merchant_selector_values(self) -> None:
        if not self.current_merchant or not hasattr(self, "merchant_specialty_list"):
            return
        for widget, selected in ((self.merchant_specialty_list, self.current_merchant.specialties), (self.merchant_affiliation_list, self.current_merchant.affiliations)):
            available = [widget.get(index) for index in range(widget.size())]
            for value in selected:
                if value not in available: widget.insert("end", value); available.append(value)
            for index, value in enumerate(available):
                if value in selected: widget.selection_set(index)

    def _merchant_add_custom_selector(self) -> None:
        values = prompt_form(self.root, "Add merchant specialty or affiliation", (
            FormField("target", "Add to", "choice", "Specialty", ("Specialty", "Affiliation")),
            FormField("value", "Name"),
        ), submit_label="Add")
        if not values or not values["value"]:
            return
        widget = self.merchant_affiliation_list if values["target"] == "Affiliation" else self.merchant_specialty_list
        existing = [widget.get(index) for index in range(widget.size())]
        if values["value"] not in existing:
            widget.insert("end", values["value"])
            existing.append(values["value"])
        widget.selection_set(existing.index(values["value"]))

    def _generate_merchant_stock(self) -> None:
        record = self._update_merchant_from_fields()
        if not self.snapshot: self.merchant_status_var.set("Catalog is unavailable."); return
        issues = generate_stock(record, self.snapshot.items, self.snapshot.digest)
        self._refresh_merchant_stock(); self.merchant_status_var.set("; ".join(issues) or f"Generated {len(record.stock)} stock lines.")

    def _merchant_tags(self, index: int | None = None) -> None:
        record = self._update_merchant_from_fields()
        tag = record.tags[index] if index is not None and 0 <= index < len(record.tags) else None
        window = tk.Toplevel(self.root); window.title("Edit merchant rule" if tag else "Add merchant rule"); window.transient(self.root); window.grab_set()
        mode_var = tk.StringVar(value=tag.mode if tag else "Favor")
        facet_var = tk.StringVar(value=tag.facet if tag else "Category")
        search_var = tk.StringVar(value=tag.selected_values()[0] if tag and tag.facet in {"Text", "Label"} else "")
        selected_values = set(tag.selected_values()) if tag else set()
        ttk.Label(window, text="Rule").grid(row=0, column=0, padx=6, pady=5, sticky="w")
        ttk.Combobox(window, textvariable=mode_var, values=("Guarantee", "Favor", "Avoid", "Forbid"), state="readonly").grid(row=0, column=1, padx=6, pady=5)
        ttk.Label(window, text="Matches").grid(row=1, column=0, padx=6, sticky="w")
        facet_box = ttk.Combobox(window, textvariable=facet_var, values=("Category", "Manufacturer", "Kind", "Rank", "Price Band", "Text", "Label"), state="readonly")
        facet_box.grid(row=1, column=1, padx=6, pady=5)
        ttk.Label(window, text="Search / custom").grid(row=2, column=0, padx=6, sticky="w")
        ttk.Entry(window, textvariable=search_var).grid(row=2, column=1, padx=6, pady=5, sticky="ew")
        values = tk.Listbox(window, selectmode="extended", exportselection=False, height=9, width=36)
        values.grid(row=3, column=0, columnspan=2, padx=6, pady=5, sticky="nsew")
        def refresh_values(*_args):
            facet = facet_var.get(); items = self.snapshot.items if self.snapshot else ()
            if facet == "Category": options = sorted({item.category for item in items}, key=natural_key)
            elif facet == "Manufacturer": options = manufacturer_options(items)
            elif facet == "Kind": options = sorted({item.kind for item in items if item.kind}, key=natural_key)
            elif facet == "Rank": options = sorted({item.rank for item in items if item.rank}, key=natural_key)
            elif facet == "Price Band": options = ["Free", "Cheap", "Working", "Professional", "Affluent", "Elite"]
            else: options = []
            needle = search_var.get().casefold(); values.delete(0, "end")
            for option in options:
                if not needle or needle in option.casefold():
                    values.insert("end", option)
                    if option in selected_values: values.selection_set("end")
        facet_var.trace_add("write", refresh_values); search_var.trace_add("write", refresh_values); refresh_values()
        def save_tag():
            picked = [values.get(index) for index in values.curselection()]
            if search_var.get().strip() and facet_var.get() in {"Text", "Label"}: picked = [search_var.get().strip()]
            if not picked: messagebox.showwarning("Tag value", "Select or enter at least one value.", parent=window); return
            updated = MerchantTag(mode=mode_var.get(), facet=facet_var.get(), value=picked[0], values=picked)
            if tag is not None and index is not None:
                record.tags[index] = updated
            else:
                record.tags.append(updated)
            self._refresh_merchant_tag_chips(); window.destroy()
        ttk.Button(window, text="Save rule", command=save_tag).grid(row=4, column=0, padx=6, pady=6, sticky="ew")
        ttk.Button(window, text="Cancel", command=window.destroy).grid(row=4, column=1, padx=6, pady=6, sticky="ew")

    def _refresh_merchant_tag_chips(self) -> None:
        if not hasattr(self, "merchant_tag_chips"): return
        for child in self.merchant_tag_chips.winfo_children(): child.destroy()
        ttk.Label(self.merchant_tag_chips, text="Stock rules:").pack(side="left")
        colors = {"Guarantee": "#177245", "Favor": "#1769aa", "Avoid": "#c06c00", "Forbid": "#a12622"}
        record = self.current_merchant
        for index, tag in enumerate(record.tags if record else []):
            label = f"{tag.mode}: {tag.facet} = {', '.join(tag.selected_values())}"
            chip = tk.Button(self.merchant_tag_chips, text=label, fg="white", bg=colors.get(tag.mode, "#555"), relief="flat", command=lambda item=index: self._merchant_tags(item))
            chip.pack(side="left", padx=3)
            chip.bind("<Button-3>", lambda _event, item=index: self._merchant_tags(item))
            ttk.Button(self.merchant_tag_chips, text="×", width=2, command=lambda item=index: self._merchant_remove_tag(item)).pack(side="left")
        ttk.Button(self.merchant_tag_chips, text="+ Rule", command=self._merchant_tags).pack(side="left", padx=4)
        preset_button = ttk.Menubutton(self.merchant_tag_chips, text="Add preset")
        preset_menu = tk.Menu(preset_button, tearoff=False)
        for label, category in MERCHANT_TAG_PRESETS:
            preset_menu.add_command(label=label, command=lambda value=category: self._merchant_add_tag_preset(value))
        preset_button["menu"] = preset_menu
        preset_button.pack(side="left")
        if record and self.snapshot:
            matches = eligible_items(self.snapshot.items, record.tags)
            ttk.Label(self.merchant_tag_chips, text=f"{len(matches)} eligible / {len({item.category for item in matches})} categories").pack(side="right")

    def _merchant_remove_tag(self, index: int) -> None:
        if self.current_merchant and 0 <= index < len(self.current_merchant.tags):
            self.current_merchant.tags.pop(index); self._refresh_merchant_tag_chips()

    def _merchant_add_tag_preset(self, category: str) -> None:
        record = self._update_merchant_from_fields(); record.tags.append(MerchantTag("Guarantee", "Category", category, [category])); self._refresh_merchant_tag_chips()

    def _merchant_price_rules(self) -> None:
        """Edit layered merchant rules as structured records, not a delimited string."""
        record = self._update_merchant_from_fields()
        window = tk.Toplevel(self.root)
        window.title("Merchant price rules")
        window.geometry("740x400")
        window.transient(self.root)
        body = ttk.Frame(window, padding=10)
        body.pack(fill="both", expand=True)
        tree = ttk.Treeview(body, columns=("Layer", "Match", "Adjustment", "Amount"), show="headings", selectmode="browse")
        for col, width in (("Layer", 125), ("Match", 280), ("Adjustment", 110), ("Amount", 95)):
            tree.heading(col, text=col)
            tree.column(col, width=width, stretch=col == "Match")
        tree.pack(fill="both", expand=True)
        rules = list(record.price_rules)

        def refresh() -> None:
            tree.delete(*tree.get_children())
            for index, rule in enumerate(rules):
                tree.insert("", "end", iid=str(index), values=(rule.layer, rule.match, rule.adjustment_type, f"{rule.amount:g}"))

        def edit(new: bool = False) -> None:
            selected = tree.selection()
            if not new and not selected:
                return
            index = int(selected[0]) if selected and not new else None
            original = rules[index] if index is not None else PriceRule("Global", "", "Percent", 0)
            values = prompt_form(window, "Add pricing rule" if new else "Edit pricing rule", (
                FormField("layer", "Rule layer", "choice", original.layer,
                          ("Global", "Affiliation", "Specialty", "Tag", "Category", "Item")),
                FormField("match", "Match value (blank for all)", value=original.match),
                FormField("type", "Adjustment type", "choice", original.adjustment_type, ("Percent", "Fixed")),
                FormField("amount", "Adjustment amount", "float", original.amount),
            ))
            if values is None:
                return
            updated = PriceRule(values["layer"], values["match"], values["type"], values["amount"])
            if index is None:
                rules.append(updated)
            else:
                rules[index] = updated
            refresh()

        def remove() -> None:
            if tree.selection():
                rules.pop(int(tree.selection()[0]))
                refresh()

        def save() -> None:
            record.price_rules = rules
            self._refresh_merchant_stock()
            self.merchant_status_var.set(f"Saved {len(rules)} price rules.")
            window.destroy()

        row = ttk.Frame(window, padding=10)
        row.pack(fill="x")
        ttk.Button(row, text="+ Add rule", command=lambda: edit(True)).pack(side="left")
        ttk.Button(row, text="Edit selected", command=edit).pack(side="left", padx=4)
        ttk.Button(row, text="Remove", command=remove).pack(side="left")
        ttk.Button(row, text="Save rules", command=save).pack(side="right")
        ttk.Button(row, text="Cancel", command=window.destroy).pack(side="right", padx=4)
        tree.bind("<Double-1>", lambda _event: edit(False))
        refresh()
        window.grab_set()

    def _merchant_haggle_rules(self) -> None:
        record = self._update_merchant_from_fields()
        rule = record.haggle_rule
        values = prompt_form(self.root, "Haggling house rules", (
            FormField("base", "Negotiation DC", "int", rule.base_dc, minimum=0),
            FormField("success", "Success discount (decimal)", "float", rule.success_discount, minimum=0, maximum=1),
            FormField("plus5", "Discount at +5 (decimal)", "float", rule.plus_five_discount, minimum=0, maximum=1),
            FormField("plus10", "Discount at +10 (decimal)", "float", rule.plus_ten_discount, minimum=0, maximum=1),
        ))
        if values is None:
            return
        rule.base_dc = values["base"]
        rule.success_discount = values["success"]
        rule.plus_five_discount = values["plus5"]
        rule.plus_ten_discount = values["plus10"]
        self.merchant_status_var.set("Haggling house rule updated.")

    def _merchant_ledger(self) -> None:
        if not self.current_merchant: return
        window = tk.Toplevel(self.root); window.title(f"Ledger — {self.current_merchant.name}"); window.geometry("720x350")
        tree = ttk.Treeview(window, columns=("Time", "Direction", "Item", "Qty", "Total", "Outcome"), show="headings")
        for col, width in (("Time", 165), ("Direction", 75), ("Item", 180), ("Qty", 45), ("Total", 75), ("Outcome", 130)):
            tree.heading(col, text=col); tree.column(col, width=width, stretch=col == "Item")
        transactions = self.merchant_store.transactions(self.current_merchant.id)
        for transaction in transactions: tree.insert("", "end", iid=transaction.id, values=(transaction.timestamp[:19], transaction.direction, transaction.item_name, transaction.quantity, f"{transaction.final_price:,.2f}", transaction.outcome))
        tree.pack(fill="both", expand=True, padx=6, pady=6)
        def reverse():
            selected = tree.selection()
            transaction = next((item for item in transactions if selected and item.id == selected[0]), None)
            if not transaction or transaction.reversal_of: return
            line = next((item for item in self.current_merchant.stock if item.id == transaction.stock_line_id), None)
            if line: line.quantity += transaction.quantity; line.sold_out = False
            self.current_merchant.cash -= transaction.final_price
            reversal = MerchantTransaction(direction="Reversal", stock_line_id=transaction.stock_line_id, item_name=transaction.item_name, quantity=transaction.quantity, unit_price=transaction.unit_price, final_price=-transaction.final_price, note=f"Reversal of {transaction.id}", reversal_of=transaction.id)
            self.merchant_store.add_transaction(self.current_merchant.id, reversal); self._save_merchant(); self._refresh_merchant_stock(); window.destroy()
        ttk.Button(window, text="Reverse selected sale", command=reverse).pack(pady=(0, 6))

    def _refresh_merchant_library(self) -> None:
        if not hasattr(self, "merchant_tree"): return
        self.merchant_tree.delete(*self.merchant_tree.get_children())
        for record in self.merchant_store.list(self.merchant_search_var.get()): self.merchant_tree.insert("", "end", iid=record.id, values=(record.name, record.record_type, record.wealth_tier, len(record.stock)), text=record.name)

    def _merchant_stock_sort_key(self, record: MerchantRecord, line: MerchantStockLine, column: str):
        if column == "Quantity": return line.quantity
        if column == "Final Price": return final_price(record, line)
        if column == "Availability": return int(line.sold_out or line.quantity <= 0)
        if column == "Locked": return int(line.locked)
        if column == "Reconciliation": return int(record.catalog_digest != (self.snapshot.digest if self.snapshot else record.catalog_digest))
        return natural_key(str(dict(line.snapshot.get("fields", [])).get(column, "")))

    def _sort_merchant_stock(self, column: str) -> None:
        self.merchant_stock_sort_descending = not self.merchant_stock_sort_descending if column == self.merchant_stock_sort_column else False
        self.merchant_stock_sort_column = column
        self._refresh_merchant_stock()

    def _refresh_merchant_stock(self) -> None:
        if not hasattr(self, "merchant_stock_panels"): return
        for child in self.merchant_stock_panels.winfo_children(): child.destroy()
        self.merchant_line_by_tree.clear()
        record = self.current_merchant
        if not record: return
        filter_category = self.merchant_stock_filter_var.get().strip() if hasattr(self, "merchant_stock_filter_var") else "All"
        text_filter = self.merchant_stock_text_filter_var.get().strip().casefold() if hasattr(self, "merchant_stock_text_filter_var") else ""
        grouped: dict[str, list[MerchantStockLine]] = {}
        for line in record.stock:
            searchable = " ".join((line.category, line.name, line.manufacturer, line.kind, line.rank, *(str(value) for _label, value in line.snapshot.get("fields", [])))).casefold()
            if (filter_category == "All" or line.category == filter_category) and (not text_filter or text_filter in searchable): grouped.setdefault(line.category, []).append(line)
        for category in sorted(grouped, key=natural_key):
            lines = sorted(grouped[category], key=lambda line: self._merchant_stock_sort_key(record, line, self.merchant_stock_sort_column), reverse=self.merchant_stock_sort_descending)
            panel = ttk.LabelFrame(self.merchant_stock_panels, text=f"{category} ({len(lines)} items)", padding=4)
            panel.pack(fill="x", expand=True, pady=(0, 5))
            fields = []
            for line in lines:
                for label, _value in line.snapshot.get("fields", []):
                    if label not in fields: fields.append(label)
            columns = tuple(fields + ["Quantity", "Final Price", "Availability", "Locked", "Reconciliation"])
            tree = ttk.Treeview(panel, columns=columns, show="headings", selectmode="extended", height=min(10, max(3, len(lines))))
            for column in columns:
                indicator = (" ▼" if self.merchant_stock_sort_descending else " ▲") if column == self.merchant_stock_sort_column else ""
                tree.heading(column, text=column + indicator, command=lambda value=column: self._sort_merchant_stock(value))
                tree.column(column, width=105 if column in fields else 85, stretch=column in {"Name", "Special Notes"})
            for line in lines:
                source = dict(line.snapshot.get("fields", []))
                state = "Sold out" if line.sold_out or line.quantity <= 0 else "Available"
                values = [source.get(field, "") for field in fields] + [line.quantity, f"{final_price(record, line):,.2f}", state, "Yes" if line.locked else "", "Current" if record.catalog_digest == (self.snapshot.digest if self.snapshot else record.catalog_digest) else "Review"]
                tree.insert("", "end", iid=line.id, values=values)
                self.merchant_line_by_tree[f"{id(tree)}:{line.id}"] = line
            tree.pack(fill="x", expand=True)
            tree.bind("<<TreeviewSelect>>", lambda _event, widget=tree: self._show_merchant_price(widget))

    def _selected_merchant_line(self) -> MerchantStockLine | None:
        if not hasattr(self, "merchant_stock_panels"): return None
        for panel in self.merchant_stock_panels.winfo_children():
            for widget in panel.winfo_children():
                if isinstance(widget, ttk.Treeview):
                    selected = widget.selection()
                    if selected: return self.merchant_line_by_tree.get(f"{id(widget)}:{selected[0]}")
        return None

    def _show_merchant_price(self, _tree=None) -> None:
        # Only one category table may own a stock selection at a time.
        if _tree is not None and _tree.selection():
            for panel in self.merchant_stock_panels.winfo_children():
                for child in panel.winfo_children():
                    if isinstance(child, ttk.Treeview) and child is not _tree and child.selection():
                        child.selection_remove(child.selection())
        line = self._selected_merchant_line()
        if not line: return
        text = "\n".join(f"{label}: {amount:,.2f}" for label, amount in price_breakdown(self.current_merchant, line))
        self.merchant_detail.configure(state="normal"); self.merchant_detail.delete("1.0", "end"); self.merchant_detail.insert("1.0", text); self.merchant_detail.configure(state="disabled")

    def _load_merchant(self) -> None:
        selected = self.merchant_tree.selection()
        record = self.merchant_store.get(selected[0]) if selected else None
        if record:
            self.current_merchant = record
            self._reconcile_merchant_if_needed()
            self._load_merchant_fields(); self.merchant_status_var.set("Loaded saved stock snapshot.")

    def _reconcile_merchant_if_needed(self) -> None:
        if not self.current_merchant or not self.snapshot:
            return
        affected = reconciliation_lines(self.current_merchant, self.snapshot.items, self.snapshot.digest)
        if not affected:
            return
        by_identity = {(item.source_gid, item.source_row): item for item in self.snapshot.items}
        by_name = {item.name.casefold(): item for item in self.snapshot.items}
        for line in list(affected):
            answer = messagebox.askyesnocancel("Reconcile merchant stock", f"{line.name} changed or is unavailable.\n\nYes: keep saved snapshot\nNo: replace with current equivalent\nCancel: stop reconciliation without removing this item", parent=self.root)
            if answer is True:
                continue
            if answer is False:
                replacement = by_identity.get((line.source_gid, line.source_row)) or by_name.get(line.name.casefold())
                if replacement:
                    index = self.current_merchant.stock.index(line); updated = MerchantStockLine.from_item(replacement, line.quantity); updated.locked = line.locked; self.current_merchant.stock[index] = updated
                    continue
                messagebox.showwarning("No equivalent found", f"No current catalog equivalent for {line.name}; it was kept.", parent=self.root)
                continue
            return  # Cancel is never destructive.
        self.current_merchant.catalog_digest = self.snapshot.digest
        self.current_merchant.updated_at = now_iso()

    def _save_merchant(self) -> None:
        record = self._update_merchant_from_fields(); self.merchant_store.save(record); self._refresh_merchant_library(); self.merchant_status_var.set("Merchant saved.")

    def _duplicate_merchant(self) -> None:
        if not self.current_merchant: return
        copy_record = MerchantRecord.from_dict(self.current_merchant.to_dict()); copy_record.id = str(uuid4()); copy_record.name += " copy"; copy_record.created_at = copy_record.updated_at = now_iso(); self.current_merchant = copy_record; self._save_merchant(); self._load_merchant_fields()

    def _delete_merchant(self) -> None:
        selected = self.merchant_tree.selection()
        if selected and messagebox.askyesno("Delete merchant?", "Delete the selected merchant or bundle and its ledger?"):
            self.merchant_store.delete(selected[0]); self._refresh_merchant_library()

    def _print_merchant(self) -> None:
        if self.current_merchant: self.spooler.enqueue([merchant_document(self._update_merchant_from_fields())], 1); self.merchant_status_var.set("Merchant sheet queued.")

    def _merchant_add_catalog(self, chosen: list[GearItem] | None = None) -> None:
        if not self.current_merchant: self._new_merchant()
        for item in (self._selected_items() if chosen is None else chosen): self.current_merchant.stock.append(MerchantStockLine.from_item(item))
        self._refresh_merchant_stock()

    def _merchant_quantity(self) -> None:
        line = self._selected_merchant_line()
        if line:
            value = simpledialog.askinteger("Stock quantity", "Quantity:", initialvalue=line.quantity, minvalue=0, maxvalue=999, parent=self.root)
            if value is not None: line.quantity = value; line.sold_out = value == 0; self._refresh_merchant_stock()

    def _merchant_sold_out(self) -> None:
        line = self._selected_merchant_line()
        if line: line.sold_out = not line.sold_out; self._refresh_merchant_stock()

    def _merchant_lock(self) -> None:
        line = self._selected_merchant_line()
        if line: line.locked = not line.locked; self._refresh_merchant_stock()

    def _merchant_item_price(self) -> None:
        line = self._selected_merchant_line()
        if not line:
            return
        override = line.item_override
        values = prompt_form(self.root, f"Price override: {line.name}", (
            FormField("type", "Adjustment type", "choice",
                override.adjustment_type if override else "Percent", ("Percent", "Fixed")),
            FormField("amount", "Adjustment amount", "float",
                override.amount if override else 0),
        ), submit_label="Apply price")
        if values is None:
            return
        line.item_override = PriceRule("Item", line.name, values["type"], values["amount"])
        self._refresh_merchant_stock()
        self._show_merchant_price()

    def _merchant_remove_line(self) -> None:
        line = self._selected_merchant_line()
        if line and messagebox.askyesno("Remove stock", f"Remove {line.name} from this merchant?", parent=self.root):
            self.current_merchant.stock.remove(line); self._refresh_merchant_stock()

    def _merchant_select_category(self) -> None:
        """Select every row in the currently visible category panel(s)."""
        if not hasattr(self, "merchant_stock_panels"): return
        for panel in self.merchant_stock_panels.winfo_children():
            for widget in panel.winfo_children():
                if isinstance(widget, ttk.Treeview): widget.selection_set(widget.get_children())

    def _merchant_bulk_state(self, sold_out: bool) -> None:
        if not self.current_merchant: return
        selected: set[str] = set()
        if hasattr(self, "merchant_stock_panels"):
            for panel in self.merchant_stock_panels.winfo_children():
                for widget in panel.winfo_children():
                    if isinstance(widget, ttk.Treeview): selected.update(widget.selection())
        lines = [line for line in self.current_merchant.stock if line.id in selected]
        if not lines:
            category = self.merchant_stock_filter_var.get() if hasattr(self, "merchant_stock_filter_var") else "All"
            lines = [line for line in self.current_merchant.stock if category == "All" or line.category == category]
        for line in lines: line.sold_out = sold_out
        self._refresh_merchant_stock()

    def _merchant_sell(self) -> None:
        line = self._selected_merchant_line()
        if not line or line.sold_out or line.quantity <= 0:
            return
        values = prompt_form(self.root, f"Sell: {line.name}", (
            FormField("quantity", "Quantity", "int", 1, minimum=1, maximum=line.quantity),
            FormField("roll", "Negotiation roll (blank for list price)", "optional_int"),
        ), submit_label="Complete sale")
        if values is None:
            return
        roll = values["roll"]
        dc, outcome, unit = haggle(self.current_merchant, line, roll) if roll is not None else (None, "List price", final_price(self.current_merchant, line))
        qty = values["quantity"]
        total = unit * qty
        line.quantity -= qty
        line.sold_out = line.quantity == 0
        self.current_merchant.cash += total
        transaction = MerchantTransaction(direction="Sale", stock_line_id=line.id,
            item_name=line.name, quantity=qty, unit_price=unit, final_price=total,
            roll=roll, dc=dc, outcome=outcome)
        self._update_merchant_from_fields()
        self.merchant_store.save_with_transaction(self.current_merchant, transaction)
        self._refresh_merchant_library()
        self._refresh_merchant_stock()
        self.merchant_status_var.set(f"{outcome}; sold {qty} for {total:,.2f}.")

    def _merchant_to_loot(self) -> None:
        line = self._selected_merchant_line()
        if not line or not self.current_encounter: self.merchant_status_var.set("Select stock and open an encounter first."); return
        self._encounter_mutate("Merchant loot", f"Added {line.name} from merchant", lambda session: session.loot.append(LootEntry(name=line.name, quantity=1, value=final_price(self.current_merchant, line), revealed=True, source_name=self.current_merchant.name)))

    def _link_merchant_to_encounter(self) -> None:
        if not self.current_encounter:
            messagebox.showwarning("No encounter", "Open an encounter session first.", parent=self.root)
            return
        records = self.merchant_store.list()
        if not records:
            messagebox.showwarning("No merchants", "Save a merchant or stock bundle first.", parent=self.root)
            return
        selected = choose_records(
            self.root, "Choose encounter merchant", records,
            (("Name", lambda item: item.name, 300), ("Type", lambda item: item.record_type, 110),
             ("Stock", lambda item: len(item.stock), 75)),
            submit_label="Link merchant",
        )
        if not selected:
            return
        record = selected[0]
        if record.record_type == "Bundle":
            record = bundle_as_merchant(record)
            self.merchant_store.save(record)
        if record.id not in self.current_encounter.merchant_ids:
            self.current_encounter.merchant_ids.append(record.id)
        self.current_encounter.active_merchant_id = record.id
        self.encounter_store.save_session(self.current_encounter)
        self.merchant_status_var.set(f"{record.name} is active for {self.current_encounter.name}.")

    def _build_encounter_ui(self, parent: ttk.Frame) -> None:
        """Build the encounter as a single combat console without notebooks."""
        style = ttk.Style()
        style.configure("Encounter.TButton", padding=(7, 4))
        style.configure("EncounterPrimary.TButton", padding=(9, 5), font=("TkDefaultFont", 9, "bold"))
        style.configure("Encounter.Treeview", rowheight=25)

        self.encounter_library_expanded = True
        self.encounter_tray_expanded = False
        self._encounter_sashes_initialized = False
        self._encounter_resolution_preview: dict[str, object] | None = None
        self._condition_roll_prompt_active = False
        self._encounter_target_ids: dict[str, str] = {}
        self.encounter_target_var = tk.StringVar()
        self.encounter_attack_die_var = tk.StringVar()
        self.encounter_resistance_die_var = tk.StringVar()
        self.encounter_resolve_status_var = tk.StringVar(value="Choose an action and target.")
        self.encounter_condition_name_var = tk.StringVar(value="Dazed")
        self.encounter_condition_duration_var = tk.StringVar(value="manual")
        self.encounter_condition_rounds_var = tk.StringVar(value="1")
        self.encounter_condition_recovery_var = tk.StringVar(value="WILL")
        self.encounter_condition_dc_var = tk.StringVar(value="10")
        self.encounter_expanded_items: dict[str, set[str]] = {}
        self.encounter_metadata_expanded: set[str] = set()
        self._encounter_text_tags: list[str] = []
        self._encounter_sheet_tag_names: dict[tuple[str, str], str] = {}
        self.encounter_attack_die_var.trace_add("write", lambda *_args: self._clear_encounter_resolution_preview())
        self.encounter_resistance_die_var.trace_add("write", lambda *_args: self._clear_encounter_resolution_preview())

        toolbar = ttk.Frame(parent, padding=(8, 6))
        toolbar.pack(fill="x")
        self.encounter_title_var = tk.StringVar(value="No active encounter")
        self.encounter_round_var = tk.StringVar(value="Round —")
        self.encounter_status_var = tk.StringVar(value="Create or resume an encounter.")
        ttk.Label(toolbar, textvariable=self.encounter_title_var,
                  font=("TkDefaultFont", 12, "bold")).pack(side="left")
        ttk.Label(toolbar, textvariable=self.encounter_round_var).pack(side="left", padx=(14, 8))
        ttk.Button(toolbar, text="Next Turn  ▶", command=self._encounter_next_turn,
                   style="EncounterPrimary.TButton").pack(side="right", padx=(4, 0))
        # Keep the manager toggle visible when a session hides the management pane.
        self.encounter_manager_button = ttk.Button(
            toolbar, text="Hide Manager", command=self._toggle_encounter_library,
            style="Encounter.TButton")
        self.encounter_manager_button.pack(side="right", padx=(4, 0))
        encounter_menu = tk.Menu(self.root, tearoff=False)
        encounter_menu.add_command(label="Quick Generate…", command=self._encounter_quick_generate)
        encounter_menu.add_command(label="Advanced Builder…", command=self._encounter_advanced_editor)
        encounter_menu.add_command(label="Show / Hide Campaign Manager", command=self._toggle_encounter_library)
        encounter_menu.add_command(label="Roster Overview", command=self._encounter_focus_window)
        encounter_menu.add_command(label="Link Merchant…", command=self._link_merchant_to_encounter)
        encounter_menu.add_separator()
        encounter_menu.add_checkbutton(label="Guarantee generated weapon",
                                       variable=self.encounter_guarantee_weapon_var)
        encounter_menu.add_command(label="Keyboard Shortcuts", command=self._show_encounter_hotkeys)
        ttk.Menubutton(toolbar, text="Encounter ▾", menu=encounter_menu).pack(side="right", padx=3)
        print_menu = tk.Menu(self.root, tearoff=False)
        print_menu.add_command(label="Print Summary", command=self._print_encounter_summary)
        print_menu.add_command(label="Print Initiative", command=self._print_encounter_initiative)
        print_menu.add_command(label="Print Combatant", command=self._print_encounter_combatant)
        ttk.Menubutton(toolbar, text="Print ▾", menu=print_menu).pack(side="right", padx=3)
        ttk.Button(toolbar, text="Redo", command=self._encounter_redo).pack(side="right", padx=1)
        ttk.Button(toolbar, text="Undo", command=self._encounter_undo).pack(side="right", padx=1)

        panes = ttk.Panedwindow(parent, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8)
        self.encounter_panes = panes

        # LEFT: management rail, roster, and encounter turn controls.
        left = ttk.Frame(panes, padding=(3, 2), width=390)
        panes.add(left, weight=4)
        self.encounter_library_body = ttk.LabelFrame(left, text="Campaign Management", padding=4)
        self.encounter_library_body.pack(fill="x", pady=(0, 4))
        library = self.encounter_library_body
        for column in range(4):
            library.columnconfigure(column, weight=1, uniform="management")
        self.enc_campaign_tree = ttk.Treeview(library, columns=("Tags",), show="tree headings", height=2, selectmode="browse", style="Encounter.Treeview")
        self.enc_campaign_tree.heading("#0", text="Campaign")
        self.enc_campaign_tree.heading("Tags", text="Tags")
        self.enc_campaign_tree.column("#0", width=120)
        self.enc_campaign_tree.column("Tags", width=65)
        self.enc_campaign_tree.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.enc_campaign_tree.bind("<<TreeviewSelect>>", self._on_encounter_campaign_selected)
        ttk.Button(library, text="New", command=self._new_campaign).grid(row=1, column=0, sticky="ew", padx=(0, 1))
        ttk.Button(library, text="Edit", command=self._edit_campaign).grid(row=1, column=1, sticky="ew", padx=1)
        ttk.Button(library, text="Party", command=self._edit_campaign_party).grid(row=1, column=2, sticky="ew", padx=1)
        ttk.Button(library, text="Delete", command=self._delete_campaign).grid(row=1, column=3, sticky="ew", padx=(1, 0))
        self.enc_template_tree = ttk.Treeview(library, columns=("Threat",), show="tree headings", height=2, selectmode="browse", style="Encounter.Treeview")
        self.enc_template_tree.heading("#0", text="Template")
        self.enc_template_tree.heading("Threat", text="Difficulty")
        self.enc_template_tree.column("#0", width=120)
        self.enc_template_tree.column("Threat", width=65)
        self.enc_template_tree.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        ttk.Button(library, text="New Template", command=self._new_encounter_template).grid(row=3, column=0, sticky="ew", padx=(0, 1))
        ttk.Button(library, text="Start", command=self._start_encounter_template).grid(row=3, column=1, sticky="ew", padx=1)
        ttk.Button(library, text="Save Current", command=self._save_session_as_template).grid(row=3, column=2, sticky="ew", padx=(1, 0))
        ttk.Button(library, text="Delete", command=self._delete_encounter_template).grid(row=3, column=3, sticky="ew", padx=(1, 0))
        self.enc_session_tree = ttk.Treeview(library, columns=("Status", "Round"), show="tree headings", height=2, selectmode="browse", style="Encounter.Treeview")
        self.enc_session_tree.heading("#0", text="Session")
        self.enc_session_tree.heading("Status", text="Status")
        self.enc_session_tree.heading("Round", text="Rnd")
        self.enc_session_tree.column("#0", width=105)
        self.enc_session_tree.column("Status", width=55)
        self.enc_session_tree.column("Round", width=35)
        self.enc_session_tree.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        self.enc_session_tree.bind("<Double-1>", lambda _event: self._load_encounter_session())
        ttk.Button(library, text="Resume", command=self._load_encounter_session, style="EncounterPrimary.TButton").grid(row=5, column=0, columnspan=2, sticky="ew", padx=(0, 1))
        ttk.Button(library, text="Archive", command=self._complete_encounter).grid(row=5, column=2, sticky="ew", padx=1)
        ttk.Button(library, text="Delete", command=self._delete_encounter_session).grid(row=5, column=3, sticky="ew", padx=(1, 0))

        roster_controls = ttk.LabelFrame(left, text="Initiative Roster", padding=4)
        self.encounter_roster_controls = roster_controls
        roster_controls.pack(fill="both", expand=True)
        add_row = ttk.Frame(roster_controls)
        add_row.pack(fill="x")
        add_menu = tk.Menu(self.root, tearoff=False)
        for label, command in (("Saved characters…", self._encounter_add_saved),
                               ("Current character", self._encounter_add_current),
                               ("Ad hoc combatant…", self._encounter_add_ad_hoc),
                               ("Initiative marker…", self._encounter_add_marker)):
            add_menu.add_command(label=label, command=command)
        ttk.Menubutton(add_row, text="+ Add combatant ▾", menu=add_menu).pack(side="left", fill="x", expand=True)
        ttk.Button(add_row, text="Remove", command=self._encounter_remove_selected).pack(side="left", padx=(4, 0))
        self.encounter_filter_var = tk.StringVar()
        search = ttk.Entry(roster_controls, textvariable=self.encounter_filter_var)
        search.pack(fill="x", pady=(3, 2))
        search.bind("<KeyRelease>", lambda _event: self._refresh_encounter_view())
        columns = ("Init", "Name", "Faction", "Bruises", "State")
        self.encounter_tree = ttk.Treeview(roster_controls, columns=columns, show="headings", selectmode="browse", height=8, style="Encounter.Treeview")
        self.encounter_tree.tag_configure("current-turn", background="#dbeafe", font=("TkDefaultFont", 9, "bold"))
        self.encounter_tree.tag_configure("heroic", font=("TkDefaultFont", 9, "underline"))
        self.encounter_tree.tag_configure("heroic-current-turn", background="#dbeafe", font=("TkDefaultFont", 9, "bold underline"))
        widths = {"Init": 38, "Name": 120, "Faction": 62, "Bruises": 48, "State": 105}
        for column in columns:
            self.encounter_tree.heading(column, text=column)
            self.encounter_tree.column(column, width=widths[column], stretch=column in {"Name", "State"})
        self.encounter_tree.pack(fill="both", expand=True)
        self.encounter_tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_encounter_inspector())
        self.encounter_tree.bind("<Double-1>", self._encounter_roster_double_click)
        self.encounter_tree.bind("<Button-1>", self._encounter_roster_target_click, add="+")
        self.encounter_tree.bind("<Button-3>", self._encounter_roster_context_menu)
        turn = ttk.Frame(roster_controls)
        turn.pack(fill="x", pady=(3, 0))
        ttk.Button(turn, text="Roll Initiative", command=self._encounter_roll_initiative).pack(side="left")
        ttk.Label(turn, text="Double-click an initiative to edit it.", foreground="#667085").pack(side="right")

        # CENTER: compact, directly editable combatant inspector.
        center = ttk.Frame(panes, padding=(4, 2), width=340)
        panes.add(center, weight=4)
        inspector = ttk.LabelFrame(center, text="Selected Combatant", padding=8)
        inspector.pack(fill="both", expand=True)
        self.encounter_actor_name_var = tk.StringVar(value="Select a combatant")
        self.encounter_actor_stats_var = tk.StringVar(value="")
        self.encounter_actor_status_var = tk.StringVar(value="")
        ttk.Label(inspector, textvariable=self.encounter_actor_name_var,
                  font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(inspector, textvariable=self.encounter_actor_stats_var, wraplength=300).pack(anchor="w", pady=(4, 1))
        ttk.Label(inspector, textvariable=self.encounter_actor_status_var,
                  foreground="#667085", wraplength=300).pack(anchor="w")
        ttk.Separator(inspector).pack(fill="x", pady=8)
        bruises = ttk.Frame(inspector)
        bruises.pack(fill="x")
        ttk.Label(bruises, text="Bruises").pack(side="left")
        self.encounter_bruise_var = tk.StringVar(value="0")
        ttk.Button(bruises, text="−", width=3, command=lambda: self._encounter_bruise(-1)).pack(side="right")
        ttk.Label(bruises, textvariable=self.encounter_bruise_var, width=4,
                  anchor="center", font=("TkDefaultFont", 11, "bold")).pack(side="right")
        ttk.Button(bruises, text="+", width=3, command=lambda: self._encounter_bruise(1)).pack(side="right")
        self.encounter_defeated_var = tk.BooleanVar()
        ttk.Checkbutton(inspector, text="Defeated", variable=self.encounter_defeated_var,
                        command=self._encounter_toggle_defeated).pack(anchor="w", pady=(5, 4))
        ttk.Separator(inspector).pack(fill="x", pady=5)
        cond_header = ttk.Frame(inspector)
        cond_header.pack(fill="x")
        ttk.Label(cond_header, text="Conditions", font=("TkDefaultFont", 9, "bold")).pack(side="left")
        ttk.Button(cond_header, text="+ Add", command=self._encounter_add_condition_dialog).pack(side="right")
        self.encounter_condition_chips = ttk.Frame(inspector)
        self.encounter_condition_chips.pack(fill="x", pady=(4, 7))
        ttk.Separator(inspector).pack(fill="x", pady=5)
        ttk.Label(inspector, text="Other Resources", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        self.encounter_compact_resources = ttk.Frame(inspector)
        self.encounter_compact_resources.pack(fill="x", pady=(4, 7))
        ttk.Separator(inspector).pack(fill="x", pady=5)
        ttk.Label(inspector, text="Action Economy", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        economy = ttk.Frame(inspector)
        economy.pack(fill="x", pady=3)
        self.encounter_economy_vars = {}
        for label, field in (("Standard", "standard_used"), ("Move", "move_used"), ("Reaction", "reaction_used")):
            var = tk.BooleanVar(value=False)
            self.encounter_economy_vars[field] = var
            ttk.Checkbutton(economy, text=label, variable=var,
                            command=lambda key=field: self._toggle_encounter_flag(key)).pack(side="left", padx=(0, 4))
        bottom = ttk.Frame(inspector)
        bottom.pack(fill="x", side="bottom", pady=(8, 0))
        ttk.Button(bottom, text="Full Sheet…", command=self._encounter_open_full_sheet).pack(side="left")
        more = tk.Menu(self.root, tearoff=False)
        more.add_command(label="Edit Notes", command=self._encounter_edit_notes)
        more.add_command(label="Add Gear…", command=self._encounter_add_gear)
        more.add_command(label="Add Resource…", command=self._encounter_add_resource)
        more.add_command(label="Refresh Character Source", command=self._encounter_refresh_source)
        more.add_separator()
        for label, field in (("Delay", "delayed"), ("Ready", "readied"),
                             ("Hold", "holding"), ("Skip", "skipped")):
            more.add_command(label=label, command=lambda key=field: self._toggle_encounter_flag(key))
        ttk.Menubutton(bottom, text="More ▾", menu=more).pack(side="right")
        # Retain the structured text/tag adapter for legacy print and context handlers.
        # It is intentionally not packed into the combat workspace.
        self.encounter_stats_text = tk.Text(center, wrap="word", height=9, state="disabled")
        self.encounter_stats_text.bind("<Button-3>", self._encounter_sheet_context_menu)
        self.encounter_stats_text.bind("<B1-Motion>", self._encounter_action_drag_motion, add="+")
        self.encounter_stats_text.bind("<ButtonRelease-1>", self._encounter_action_drag_release, add="+")
        self.encounter_stats_text.bind("<Escape>", self._cancel_encounter_action_drag, add="+")
        # RIGHT: action-first work area with persistent target and dice controls.
        right = ttk.Frame(panes, padding=(4, 2), width=520)
        panes.add(right, weight=5)
        self.encounter_action_picker_var = tk.StringVar()
        self.encounter_action_picker = ttk.Combobox(right, textvariable=self.encounter_action_picker_var,
                                                     state="readonly")

        resolver = ttk.LabelFrame(right, text="Resolve Selected Action", padding=5)
        resolver.pack(fill="x", side="bottom")
        resolver.columnconfigure(1, weight=1)
        ttk.Label(resolver, text="Target").grid(row=0, column=0, sticky="w")
        self.encounter_target_box = ttk.Combobox(resolver, textvariable=self.encounter_target_var, state="readonly")
        self.encounter_target_box.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(4, 0))
        self.encounter_pick_target_button = ttk.Button(resolver, text="Pick in roster", command=self._encounter_toggle_target_mode)
        self.encounter_pick_target_button.grid(row=0, column=3, sticky="ew", padx=(4, 0))
        self.encounter_target_box.bind("<<ComboboxSelected>>", lambda _event: self._clear_encounter_resolution_preview())
        self.encounter_target_box.bind("<Return>", lambda _event: (self._encounter_resolve_action(), "break")[1])
        self.encounter_target_box.bind("<Escape>", lambda _event: (self._clear_encounter_resolution_preview(), "break")[1])
        ttk.Label(resolver, text="Attack d20").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.encounter_attack_die_entry = ttk.Entry(resolver, textvariable=self.encounter_attack_die_var, width=5)
        self.encounter_attack_die_entry.grid(row=1, column=1, sticky="w", padx=4, pady=(4, 0))
        ttk.Label(resolver, text="Resistance d20").grid(row=1, column=2, sticky="e", pady=(4, 0))
        self.encounter_resistance_die_entry = ttk.Entry(resolver, textvariable=self.encounter_resistance_die_var, width=5)
        self.encounter_resistance_die_entry.grid(row=1, column=3, sticky="w", padx=(4, 0), pady=(4, 0))
        for entry in (self.encounter_attack_die_entry, self.encounter_resistance_die_entry):
            entry.bind("<Return>", lambda _event: (self._encounter_resolve_action(), "break")[1])
            entry.bind("<Escape>", lambda _event: (self._clear_encounter_resolution_preview(), "break")[1])
        ttk.Label(resolver, textvariable=self.encounter_resolve_status_var, wraplength=330).grid(row=2, column=0, columnspan=4, sticky="ew", pady=5)
        buttons = ttk.Frame(resolver)
        buttons.grid(row=3, column=0, columnspan=4, sticky="ew")
        for column in range(3):
            buttons.columnconfigure(column, weight=1)
        ttk.Button(buttons, text="Preview / Roll", command=self._encounter_preview_action, style="EncounterPrimary.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 2))
        self.encounter_apply_button = ttk.Button(buttons, text="Apply Suggested", command=self._encounter_apply_action_preview, state="disabled")
        self.encounter_apply_button.grid(row=0, column=1, sticky="ew", padx=2)
        self.encounter_log_button = ttk.Button(buttons, text="Log Only", command=self._encounter_log_action_preview, state="disabled")
        self.encounter_log_button.grid(row=0, column=2, sticky="ew", padx=(2, 0))

        # Four independently scrollable sections, with draggable horizontal and
        # vertical dividers. The resolver remains pinned below the grid.
        sections = ttk.Panedwindow(right, orient="vertical")
        sections.pack(fill="both", expand=True)
        upper = ttk.Panedwindow(sections, orient="horizontal")
        lower_row = ttk.Panedwindow(sections, orient="horizontal")
        sections.add(upper, weight=1)
        sections.add(lower_row, weight=1)
        self.encounter_sections = sections
        self.encounter_sections_upper = upper
        self.encounter_sections_lower = lower_row
        self._encounter_sections_sashes_initialized = False

        actions = ttk.LabelFrame(upper, text="Weapons / Actions", padding=3)
        self.encounter_actions_panel = actions
        upper.add(actions, weight=1)
        action_rows = ttk.Frame(actions)
        action_rows.pack(fill="both", expand=True)
        self.encounter_action_tree = ttk.Treeview(action_rows, columns=("Attack", "DC", "Ammo", "Available"), show="tree headings", height=4, style="Encounter.Treeview")
        self.encounter_action_tree.heading("#0", text="Action")
        self.encounter_action_tree.column("#0", width=115, minwidth=65, stretch=True)
        for column, width in (("Attack", 85), ("DC", 42), ("Ammo", 105), ("Available", 83)):
            self.encounter_action_tree.heading(column, text=column)
            self.encounter_action_tree.column(column, width=width, stretch=column == "Ammo")
        self.encounter_action_tree.pack(side="left", fill="both", expand=True)
        actions_vscroll = ttk.Scrollbar(action_rows, orient="vertical", command=self.encounter_action_tree.yview)
        actions_vscroll.pack(side="right", fill="y")
        actions_hscroll = ttk.Scrollbar(actions, orient="horizontal", command=self.encounter_action_tree.xview)
        actions_hscroll.pack(fill="x")
        self.encounter_action_tree.configure(yscrollcommand=actions_vscroll.set, xscrollcommand=actions_hscroll.set)
        self.encounter_action_tree.tag_configure("insufficient-resource", foreground="#c62828")
        self.encounter_action_tree.bind("<<TreeviewSelect>>", lambda _event: (self._clear_encounter_resolution_preview(), self._refresh_encounter_targets(self._selected_encounter_combatant()), self._refresh_encounter_selected_ammo()))
        self.encounter_action_tree.bind("<ButtonPress-1>", self._encounter_action_drag_start)
        self.encounter_action_tree.bind("<B1-Motion>", self._encounter_action_drag_motion)
        self.encounter_action_tree.bind("<ButtonRelease-1>", self._encounter_action_drag_release)
        self.encounter_action_tree.bind("<Double-1>", self._encounter_action_double_click_ui)
        self.encounter_action_tree.bind("<Escape>", self._cancel_encounter_action_drag)
        row = ttk.Frame(actions)
        row.pack(fill="x", pady=(2, 0))
        self.encounter_ammo_detail_var = tk.StringVar(value="Select a weapon to view ammunition.")
        ttk.Label(row, textvariable=self.encounter_ammo_detail_var, wraplength=320).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Ammo −", command=lambda: self._encounter_adjust_selected_ammo(-1)).pack(side="right")
        ttk.Button(row, text="Ammo +", command=lambda: self._encounter_adjust_selected_ammo(1)).pack(side="right", padx=2)
        action_more = tk.Menu(self.root, tearoff=False)
        action_more.add_command(label="New Action…", command=self._encounter_new_action)
        action_more.add_command(label="Delete Selected", command=self._encounter_delete_action)
        action_more.add_command(label="Print Selected", command=self._print_encounter_action)
        action_more.add_command(label="Publish Actions", command=self._encounter_publish_actions)
        ttk.Menubutton(actions, text="Action options ▾", menu=action_more).pack(anchor="e", pady=(4, 0))

        bionics_panel = ttk.LabelFrame(upper, text="Bionics", padding=3)
        upper.add(bionics_panel, weight=1)
        self.encounter_bionics_panel = bionics_panel
        self.encounter_bionics_tree = self._build_encounter_section_tree(
            bionics_panel, (("Type", 85), ("Effects", 175)), first_width=130)
        self.encounter_bionics_tree.bind(
            "<Double-1>", lambda event: self._encounter_section_open_action(event, "bionic"))

        inventory_panel = ttk.LabelFrame(lower_row, text="Inventory", padding=3)
        lower_row.add(inventory_panel, weight=1)
        self.encounter_inventory_panel = inventory_panel
        self.encounter_inventory_tree = self._build_encounter_section_tree(
            inventory_panel, (("Qty", 40), ("Category", 95), ("Notes", 150)), first_width=125)
        self.encounter_inventory_tree.bind(
            "<Double-1>", lambda event: self._encounter_section_open_action(event, "inventory"))

        powers_panel = ttk.LabelFrame(lower_row, text="Racial Abilities / Special Powers", padding=3)
        lower_row.add(powers_panel, weight=1)
        self.encounter_powers_panel = powers_panel
        self.encounter_powers_tree = self._build_encounter_section_tree(
            powers_panel, (("Type", 68), ("Details", 235)), first_width=145)
        self.encounter_powers_tree.bind(
            "<Double-1>", lambda event: self._encounter_section_open_action(event, "power"))

        conditions = ttk.LabelFrame(right, text="Conditions", padding=3)
        conditions.pack(fill="x", pady=3)
        self.encounter_condition_tree = ttk.Treeview(conditions, columns=("Source", "Duration"), show="tree headings", height=2, style="Encounter.Treeview")
        self.encounter_condition_tree.heading("#0", text="Condition")
        self.encounter_condition_tree.heading("Source", text="Source")
        self.encounter_condition_tree.heading("Duration", text="Duration")
        self.encounter_condition_tree.pack(fill="x")
        quick = ttk.Frame(conditions)
        quick.pack(fill="x", pady=(2, 0))
        self.encounter_condition_name_box = ttk.Combobox(quick, textvariable=self.encounter_condition_name_var, values=STANDARD_CONDITIONS, width=12)
        self.encounter_condition_name_box.pack(side="left", fill="x", expand=True)
        self.encounter_condition_duration_box = ttk.Combobox(quick, textvariable=self.encounter_condition_duration_var, values=("manual", "start_turn", "end_turn", "fixed_rounds", "save_ends", "scene", "permanent"), state="readonly", width=11)
        self.encounter_condition_duration_box.pack(side="left", padx=2)
        self.encounter_condition_duration_box.bind("<<ComboboxSelected>>", lambda _event: self._update_condition_fields())
        ttk.Button(quick, text="Add", command=self._encounter_add_condition).pack(side="left", padx=(2, 0))
        condition_details = ttk.Frame(conditions)
        condition_details.pack(fill="x")
        ttk.Label(condition_details, text="Rounds").pack(side="left")
        self.encounter_condition_rounds_entry = ttk.Entry(condition_details, textvariable=self.encounter_condition_rounds_var, width=4)
        self.encounter_condition_rounds_entry.pack(side="left", padx=(2, 7))
        ttk.Label(condition_details, text="Save").pack(side="left")
        self.encounter_condition_recovery_box = ttk.Combobox(condition_details, textvariable=self.encounter_condition_recovery_var, values=DEFENSE_KEYS, state="readonly", width=6)
        self.encounter_condition_recovery_box.pack(side="left", padx=2)
        ttk.Label(condition_details, text="DC").pack(side="left")
        self.encounter_condition_dc_entry = ttk.Entry(condition_details, textvariable=self.encounter_condition_dc_var, width=4)
        self.encounter_condition_dc_entry.pack(side="left", padx=(2, 0))
        row = ttk.Frame(conditions)
        row.pack(fill="x")
        for label, command in (("Tick", self._encounter_force_condition_tick), ("Remove", self._encounter_remove_condition), ("Recover", self._encounter_recover_condition), ("Print", self._print_encounter_condition)):
            ttk.Button(row, text=label, command=command).pack(side="left", fill="x", expand=True, padx=1)

        lower = ttk.Frame(right)
        lower.pack(fill="both", expand=True)
        lower.columnconfigure(0, weight=1, uniform="encounter-lower")
        lower.columnconfigure(1, weight=1, uniform="encounter-lower")
        resources = ttk.LabelFrame(lower, text="Resources", padding=3)
        resources.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        self.encounter_resource_tree = ttk.Treeview(resources, columns=("Current", "Max"), show="tree headings", height=1, style="Encounter.Treeview")
        self.encounter_resource_tree.heading("#0", text="Resource")
        self.encounter_resource_tree.heading("Current", text="Now")
        self.encounter_resource_tree.heading("Max", text="Max")
        self.encounter_resource_tree.pack(fill="both", expand=True)
        row = ttk.Frame(resources)
        row.pack(fill="x")
        for label, command in (("Add", self._encounter_add_resource), ("+", lambda: self._encounter_resource(1)), ("−", lambda: self._encounter_resource(-1))):
            ttk.Button(row, text=label, command=command).pack(side="left", fill="x", expand=True)
        gear = ttk.LabelFrame(lower, text="Gear", padding=3)
        gear.grid(row=0, column=1, sticky="nsew", padx=(2, 0))
        self.encounter_gear_tree = ttk.Treeview(gear, columns=("Section", "Cost", "Cap"), show="tree headings", height=1, style="Encounter.Treeview")
        self.encounter_gear_tree.heading("#0", text="Gear")
        for column in ("Section", "Cost", "Cap"):
            self.encounter_gear_tree.heading(column, text=column)
            self.encounter_gear_tree.column(column, width=45)
        self.encounter_gear_tree.pack(fill="both", expand=True)
        row = ttk.Frame(gear)
        row.pack(fill="x")
        ttk.Button(row, text="Add…", command=self._encounter_add_gear).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Remove", command=self._encounter_remove_gear).pack(side="left", fill="x", expand=True)
        notes = ttk.LabelFrame(lower, text="Notes & Character", padding=3)
        notes.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(3, 0))
        self.encounter_notes_text = tk.Text(notes, wrap="word", height=1)
        self.encounter_notes_text.pack(fill="both", expand=True)
        row = ttk.Frame(notes)
        row.pack(fill="x")
        ttk.Button(row, text="Save Notes", command=self._encounter_save_notes, style="EncounterPrimary.TButton").pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Print Combatant", command=self._print_encounter_combatant).pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(row, text="Refresh Source", command=self._encounter_refresh_source).pack(side="left", fill="x", expand=True)

        # Keep secondary tables alive as adapters for legacy commands, while
        # the right pane displays actions, ammo, and the resolver exclusively.
        self.encounter_context_controls = right
        conditions.pack_forget()
        lower.pack_forget()

        self._build_encounter_tray(parent)
        footer = ttk.Frame(parent, padding=(8, 5))
        self.encounter_footer = footer
        footer.pack(fill="x", side="bottom")
        ttk.Label(footer, textvariable=self.encounter_status_var).pack(side="left", fill="x", expand=True)
        ttk.Button(footer, text="Encounter Details", command=self._toggle_encounter_tray).pack(side="right")
        ttk.Button(footer, text="History", command=self._toggle_encounter_history).pack(side="right", padx=3)
        self.encounter_history_frame = ttk.LabelFrame(parent, text="Recent Events", padding=4)
        self.encounter_history_text = tk.Text(self.encounter_history_frame, height=4, state="disabled", wrap="word")
        self.encounter_history_text.pack(fill="x")
        panes.pack_forget()
        panes.pack(fill="both", expand=True, padx=8)
        self._update_condition_fields()
        self._refresh_encounter_library(preserve_campaign=False)
        parent.bind("<Configure>", lambda _event: self._set_initial_encounter_sashes(), add="+")
        self.root.after_idle(self._set_initial_encounter_sashes)
        self.root.after(150, self._finalize_encounter_sashes)
        parent.bind("<Configure>", lambda _event: self._set_encounter_sections_sashes(), add="+")
        self.root.after_idle(self._set_encounter_sections_sashes)

    @staticmethod
    def _build_encounter_section_tree(parent: ttk.LabelFrame, columns: tuple[tuple[str, int], ...],
                                      *, first_width: int) -> ttk.Treeview:
        """Create a scrollable character-content list that resizes with its pane."""
        body = ttk.Frame(parent)
        body.pack(fill="both", expand=True)
        keys = tuple(name for name, _width in columns)
        tree = ttk.Treeview(body, columns=keys, show="tree headings", height=4,
                            selectmode="browse", style="Encounter.Treeview")
        tree.heading("#0", text="Entry")
        tree.column("#0", width=first_width, minwidth=60, stretch=True)
        for name, width in columns:
            tree.heading(name, text=name)
            tree.column(name, width=width, minwidth=35, stretch=name in {"Effects", "Notes", "Details"})
        y_scroll = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=y_scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        y_scroll.pack(side="right", fill="y")
        x_scroll = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        x_scroll.pack(fill="x")
        tree.configure(xscrollcommand=x_scroll.set)
        return tree

    def _set_encounter_sections_sashes(self) -> None:
        """Set a balanced initial 2x2 layout once; preserve dragged sash positions."""
        if not hasattr(self, "encounter_sections") or self._encounter_sections_sashes_initialized:
            return
        width = self.encounter_sections.winfo_width()
        height = self.encounter_sections.winfo_height()
        if width < 320 or height < 170:
            return
        try:
            self.encounter_sections.sashpos(0, height // 2)
            self.encounter_sections_upper.sashpos(0, width // 2)
            self.encounter_sections_lower.sashpos(0, width // 2)
            self._encounter_sections_sashes_initialized = True
        except tk.TclError:
            return

    def _refresh_encounter_quadrants(self, item: Combatant | None) -> None:
        """Populate all four visible sections from the selected combatant."""
        if not hasattr(self, "encounter_bionics_tree"):
            return
        content = encounter_panel_content(item)
        trees = (self.encounter_bionics_tree, self.encounter_inventory_tree,
                 self.encounter_powers_tree)
        states = [self._capture_tree_state(tree) for tree in trees]
        for tree in trees:
            tree.delete(*tree.get_children())

        for gear in content.bionics:
            effect = (gear.fields.get("Effects") or gear.fields.get("Effect")
                      or gear.fields.get("Special Notes") or "; ".join(gear.notes))
            self.encounter_bionics_tree.insert("", "end", iid=gear.id, text=gear.name,
                values=(gear.fields.get("Type", "Bionic"), effect or "Passive"))

        for gear in content.inventory:
            detail = (gear.fields.get("Effect") or gear.fields.get("Effects")
                      or gear.fields.get("Special Notes") or "; ".join(gear.notes))
            self.encounter_inventory_tree.insert("", "end", iid=gear.id, text=gear.name,
                values=(gear.quantity, gear.category, detail))

        for index, trait in enumerate(content.racial_traits):
            name, details = feature_label(trait)
            self.encounter_powers_tree.insert("", "end", iid=f"trait:{index}", text=name,
                values=("Racial", details))
        for action in content.special_actions:
            details = action.notes or f"{action.effect_type} · DC {action.resistance_dc}"
            self.encounter_powers_tree.insert("", "end", iid=f"power:{action.id}", text=action.name,
                values=("Power", details))

        self.encounter_actions_panel.configure(text=f"Weapons / Actions ({len(content.actions)})")
        self.encounter_bionics_panel.configure(text=f"Bionics ({len(content.bionics)})")
        self.encounter_inventory_panel.configure(text=f"Inventory ({len(content.inventory)})")
        self.encounter_powers_panel.configure(
            text=f"Racial Abilities / Special Powers ({len(content.racial_traits) + len(content.special_actions)})")
        for tree, state in zip(trees, states):
            self._restore_tree_state(tree, state)

    def _encounter_section_open_action(self, event, section: str) -> str:
        """Let an equipment or power entry jump to its executable action."""
        tree = {"bionic": self.encounter_bionics_tree,
                "inventory": self.encounter_inventory_tree,
                "power": self.encounter_powers_tree}[section]
        selected_id = tree.identify_row(event.y)
        actor = self._selected_encounter_combatant()
        if not actor or not selected_id:
            return "break"
        if section == "bionic":
            source = f"Bionic:{selected_id}"
            action = next((entry for entry in actor.actions if entry.source == source), None)
        elif section == "inventory":
            source = f"Gear:{selected_id}"
            action = next((entry for entry in actor.actions if entry.source == source), None)
        else:
            action_id = selected_id.removeprefix("power:") if selected_id.startswith("power:") else ""
            action = next((entry for entry in actor.actions if entry.id == action_id), None)
        if action and self.encounter_action_tree.exists(action.id):
            self._select_encounter_sheet_action(action.id)
            self.encounter_action_tree.see(action.id)
            self.encounter_status_var.set(f"Selected {action.name}. Choose a target to resolve it.")
        else:
            row = tree.item(selected_id)
            detail = " · ".join(str(value) for value in row.get("values", ()) if str(value))
            self.encounter_status_var.set(f"{row.get('text', '')}: {detail}")
        return "break"

    def _encounter_roster_target_click(self, event) -> str | None:
        """In targeting mode the roster picks a defender without changing actor."""
        if not getattr(self, "_encounter_target_mode", False):
            return None
        identity = self.encounter_tree.identify_row(event.y)
        label = next((name for name, target_id in self._encounter_target_ids.items()
                      if target_id == identity), "")
        if label:
            self.encounter_target_var.set(label)
            self._clear_encounter_resolution_preview()
            self.encounter_resolve_status_var.set(f"Target: {label}. Preview the selected action.")
            self._encounter_target_mode = False
            self.encounter_pick_target_button.configure(text="Pick in roster")
        return "break"

    def _encounter_toggle_target_mode(self) -> None:
        if not self._selected_encounter_combatant() or not self.encounter_action_tree.selection():
            self.encounter_status_var.set("Select an attacker and an action first.")
            return
        self._encounter_target_mode = not getattr(self, "_encounter_target_mode", False)
        self.encounter_pick_target_button.configure(
            text="Cancel target" if self._encounter_target_mode else "Pick in roster")
        self.encounter_status_var.set(
            "Click a defender in the initiative roster; your attacker stays selected."
            if self._encounter_target_mode else "Target selection cancelled.")

    def _encounter_roster_double_click(self, event) -> str | None:
        """Double-click an initiative cell to edit directly. Other cells select."""
        identity = self.encounter_tree.identify_row(event.y)
        if not identity or self.encounter_tree.identify_column(event.x) != "#1":
            return None
        bounds = self.encounter_tree.bbox(identity, "Init")
        if not bounds:
            return "break"
        entity = next((entry for entry in
                       [*(self.current_encounter.combatants if self.current_encounter else []),
                        *(self.current_encounter.objectives if self.current_encounter else [])]
                       if entry.id == identity), None)
        if entity is None:
            return "break"
        editor = ttk.Entry(self.encounter_tree, width=7)
        editor.insert(0, str(entity.initiative if entity.initiative is not None else 0))
        x, y, width, height = bounds
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        editor.selection_range(0, "end")
        finished = False

        def finish(save: bool) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            raw = editor.get().strip()
            editor.destroy()
            if not save:
                return
            try:
                value = int(raw)
                if not -100 <= value <= 200:
                    raise ValueError
            except ValueError:
                self.encounter_status_var.set("Initiative must be an integer between -100 and 200.")
                return
            if value == entity.initiative:
                return
            def operation(session: EncounterSession) -> None:
                entries = [*session.combatants, *session.objectives]
                next(entry for entry in entries if entry.id == identity).initiative = value
            self._encounter_mutate("Initiative", f"Set {entity.name} initiative to {value}",
                                   operation, actor_id=identity)

        editor.bind("<Return>", lambda _event: (finish(True), "break")[1])
        editor.bind("<Escape>", lambda _event: (finish(False), "break")[1])
        editor.bind("<FocusOut>", lambda _event: finish(True))
        return "break"

    def _encounter_action_double_click_ui(self, event) -> str:
        """Select actions on double-click; ammo-count cells edit in place."""
        action_id = self.encounter_action_tree.identify_row(event.y)
        if not action_id:
            return "break"
        self.encounter_action_tree.selection_set(action_id)
        if self.encounter_action_tree.identify_column(event.x) != "#4":
            return "break"
        item = self._selected_encounter_combatant()
        action = next((entry for entry in item.actions if entry.id == action_id), None) if item else None
        info = weapon_ammo(item, action) if item and action else None
        bounds = self.encounter_action_tree.bbox(action_id, "Available")
        if not info or not bounds:
            return "break"
        editor = ttk.Entry(self.encounter_action_tree, width=8)
        editor.insert(0, str(info.available))
        x, y, width, height = bounds
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        editor.selection_range(0, "end")
        finished = False

        def finish(save: bool) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            raw = editor.get().strip()
            editor.destroy()
            if not save:
                return
            try:
                total = int(raw)
                if total < 0 or total > 99999:
                    raise ValueError
            except ValueError:
                self.encounter_status_var.set("Ammunition quantity must be between 0 and 99999.")
                return
            self._encounter_adjust_selected_ammo(total - info.available, action_id)

        editor.bind("<Return>", lambda _event: (finish(True), "break")[1])
        editor.bind("<Escape>", lambda _event: (finish(False), "break")[1])
        editor.bind("<FocusOut>", lambda _event: finish(True))
        return "break"

    def _encounter_adjust_selected_ammo(self, delta: int, action_id: str = "") -> None:
        item = self._selected_encounter_combatant()
        selected = self.encounter_action_tree.selection()
        selected_id = action_id or (selected[0] if selected else "")
        action = next((entry for entry in item.actions if entry.id == selected_id), None) if item else None
        info = weapon_ammo(item, action) if item and action else None
        if not item or not action or not info or delta == 0:
            return
        # Preserve the weapon by source because derived action IDs can change.
        source, name = action.source, action.name

        def operation(session: EncounterSession) -> None:
            actor = next(entry for entry in session.combatants if entry.id == item.id)
            current_action = next(entry for entry in actor.actions if entry.id == selected_id)
            change_weapon_ammo(actor, current_action, delta)
            self._sync_encounter_inventory_loot(session, actor)

        self._encounter_mutate("Ammo", f"{item.name}: adjusted {info.name} by {delta:+d}",
                               operation, actor_id=item.id)
        updated = self._selected_encounter_combatant()
        matching = next((entry for entry in updated.actions if entry.source == source and entry.name == name), None) if updated else None
        if matching and self.encounter_action_tree.exists(matching.id):
            self.encounter_action_tree.selection_set(matching.id)
            self.encounter_action_tree.see(matching.id)
        self._refresh_encounter_selected_ammo()

    def _refresh_encounter_selected_ammo(self) -> None:
        if not hasattr(self, "encounter_ammo_detail_var"):
            return
        item = self._selected_encounter_combatant()
        selected = self.encounter_action_tree.selection()
        action = next((entry for entry in item.actions if selected and entry.id == selected[0]), None) if item else None
        info = weapon_ammo(item, action) if item and action else None
        self.encounter_ammo_detail_var.set(
            info.summary + ("  ·  EMPTY" if info.empty else "") if info
            else "Choose a weapon to adjust its ammunition."
        )

    def _encounter_remove_condition_id(self, condition_id: str) -> None:
        item = self._selected_encounter_combatant()
        if not item:
            return
        def operation(session: EncounterSession) -> None:
            actor = next(entry for entry in session.combatants if entry.id == item.id)
            condition = next(entry for entry in actor.conditions if entry.id == condition_id)
            RulesEngine.deactivate_condition(actor, condition)
            actor.conditions.remove(condition)
        self._encounter_mutate("Condition", f"Removed condition from {item.name}", operation, actor_id=item.id)

    def _encounter_change_resource_id(self, resource_id: str, delta: int) -> None:
        item = self._selected_encounter_combatant()
        if not item:
            return
        def operation(session: EncounterSession) -> None:
            actor = next(entry for entry in session.combatants if entry.id == item.id)
            resource = next(entry for entry in actor.resources if entry.id == resource_id)
            resource.current = max(0, min(resource.maximum if resource.maximum is not None else 999999,
                                          resource.current + delta))
        self._encounter_mutate("Resource", f"{item.name} resource {delta:+d}",
                               operation, actor_id=item.id)

    def _refresh_encounter_compact_inspector(self, item: Combatant | None,
                                             selected_entity: Combatant | Objective | None) -> None:
        if not hasattr(self, "encounter_actor_name_var"):
            return
        for frame in (self.encounter_condition_chips, self.encounter_compact_resources):
            for child in frame.winfo_children():
                child.destroy()
        if item is None:
            self.encounter_actor_name_var.set(selected_entity.name if selected_entity else "Select a combatant")
            self.encounter_actor_stats_var.set(
                f"{selected_entity.kind} · Progress {selected_entity.progress}/{selected_entity.target}"
                if isinstance(selected_entity, Objective) else "")
            self.encounter_actor_status_var.set("Choose a combatant to edit its status.")
            self.encounter_bruise_var.set("—")
            self.encounter_defeated_var.set(False)
            for variable in self.encounter_economy_vars.values():
                variable.set(False)
            return
        abilities, defenses, speed = RulesEngine.stats(item)
        self.encounter_actor_name_var.set(f"{item.name} · PL {item.power_level}")
        self.encounter_actor_stats_var.set(
            "  ·  ".join(f"{key.title()} {defenses.get(key, 0)}"
                        for key in ("DODGE", "PARRY", "TOUGH", "FORT", "WILL")))
        active_flags = [name for key, name in
                        (("delayed", "Delayed"), ("readied", "Readied"),
                         ("holding", "Holding"), ("skipped", "Skipped"))
                        if getattr(item, key)]
        status = "Defeated" if item.defeated else " · ".join(active_flags) or "Ready"
        self.encounter_actor_status_var.set(f"{item.faction} · {status} · Speed {speed}")
        self.encounter_bruise_var.set(str(item.bruises))
        self.encounter_defeated_var.set(item.defeated)
        for field, variable in self.encounter_economy_vars.items():
            variable.set(getattr(item, field))
        conditions = [condition for condition in item.conditions if condition.active and not condition.suppressed]
        if not conditions:
            ttk.Label(self.encounter_condition_chips, text="None", foreground="#667085").pack(anchor="w")
        for index, condition in enumerate(conditions):
            button = ttk.Button(self.encounter_condition_chips, text=f"{condition.name}  ×",
                command=lambda key=condition.id: self._encounter_remove_condition_id(key))
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=2, pady=2)
        for column in range(2):
            self.encounter_condition_chips.columnconfigure(column, weight=1)
        if not item.resources:
            ttk.Label(self.encounter_compact_resources, text="None", foreground="#667085").pack(anchor="w")
        for resource in item.resources:
            line = ttk.Frame(self.encounter_compact_resources)
            line.pack(fill="x", pady=1)
            ttk.Label(line, text=resource.name).pack(side="left", fill="x", expand=True)
            ttk.Button(line, text="+", width=3, command=lambda key=resource.id: self._encounter_change_resource_id(key, 1)).pack(side="right")
            limit = f"/{resource.maximum}" if resource.maximum is not None else ""
            ttk.Label(line, text=f"{resource.current}{limit}", width=9, anchor="center").pack(side="right")
            ttk.Button(line, text="−", width=3, command=lambda key=resource.id: self._encounter_change_resource_id(key, -1)).pack(side="right")
        if hasattr(self, "encounter_full_sheet_window") and self.encounter_full_sheet_window.winfo_exists():
            self._encounter_update_full_sheet()

    def _encounter_open_full_sheet(self) -> None:
        if not self.current_encounter:
            return
        window = getattr(self, "encounter_full_sheet_window", None)
        if window is None or not window.winfo_exists():
            window = tk.Toplevel(self.root)
            window.title("Full Character Sheet")
            window.geometry("760x640")
            self.encounter_full_sheet_window = window
            frame = ttk.Frame(window, padding=8)
            frame.pack(fill="both", expand=True)
            self.encounter_full_sheet_text = tk.Text(frame, wrap="word", state="disabled",
                                                     font=("Consolas", 10))
            scrollbar = ttk.Scrollbar(frame, command=self.encounter_full_sheet_text.yview)
            self.encounter_full_sheet_text.configure(yscrollcommand=scrollbar.set)
            self.encounter_full_sheet_text.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
        self._encounter_update_full_sheet()
        window.lift()

    def _encounter_update_full_sheet(self) -> None:
        selected = self._selected_encounter_combatant() or self._selected_encounter_objective()
        if not hasattr(self, "encounter_full_sheet_window") or not self.encounter_full_sheet_window.winfo_exists():
            return
        text, _tags = self._encounter_sheet_text(selected, self.current_encounter)
        editor = self.encounter_full_sheet_text
        scroll = editor.yview()[0]
        editor.configure(state="normal")
        editor.delete("1.0", "end")
        editor.insert("1.0", text)
        editor.configure(state="disabled")
        editor.yview_moveto(scroll)

    def _toggle_encounter_history(self) -> None:
        if self.encounter_history_frame.winfo_manager():
            self.encounter_history_frame.pack_forget()
        else:
            self.encounter_history_frame.pack(fill="x", padx=8, pady=3,
                                              before=self.encounter_footer)
            self._refresh_encounter_history()

    def _refresh_encounter_history(self) -> None:
        if not hasattr(self, "encounter_history_text"):
            return
        session = self.current_encounter
        events = self.encounter_store.list_events(session.id)[-5:] if session else []
        self.encounter_history_text.configure(state="normal")
        self.encounter_history_text.delete("1.0", "end")
        self.encounter_history_text.insert("1.0", "\n".join(
            f"Round {event.round}: {event.summary}" for event in reversed(events))
            if events else "No encounter events yet.")
        self.encounter_history_text.configure(state="disabled")

    def _build_encounter_tray(self, parent: ttk.Frame) -> None:
        """Build encounter-wide controls as one collapsible, non-tabbed tray."""
        self.encounter_tray = ttk.LabelFrame(parent, text="Encounter Details", padding=4)
        for column in range(4):
            self.encounter_tray.columnconfigure(column, weight=1, uniform="encounter-tray")

        loot = ttk.LabelFrame(self.encounter_tray, text="Loot", padding=2)
        loot.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        self.encounter_loot_tree = ttk.Treeview(loot, columns=("Source", "Qty", "Value", "State"), show="tree headings", height=4, style="Encounter.Treeview")
        self.encounter_loot_tree.heading("#0", text="Loot")
        for column in ("Source", "Qty", "Value", "State"):
            self.encounter_loot_tree.heading(column, text=column)
            self.encounter_loot_tree.column(column, width=48)
        self.encounter_loot_tree.pack(fill="both", expand=True)
        row = ttk.Frame(loot)
        row.pack(fill="x")
        for label, command in (("Add", self._encounter_add_loot), ("Reveal", self._encounter_reveal_loot), ("Exclude", self._encounter_exclude_loot), ("Print", self._print_encounter_loot)):
            ttk.Button(row, text=label, command=command).pack(side="left", fill="x", expand=True)

        objectives = ttk.LabelFrame(self.encounter_tray, text="Objectives / Hazards", padding=2)
        objectives.grid(row=0, column=1, sticky="nsew", padx=2)
        self.encounter_objective_tree = ttk.Treeview(objectives, columns=("Progress", "State", "Timer"), show="tree headings", height=4, style="Encounter.Treeview")
        self.encounter_objective_tree.heading("#0", text="Objective")
        for column in ("Progress", "State", "Timer"):
            self.encounter_objective_tree.heading(column, text=column)
            self.encounter_objective_tree.column(column, width=52)
        self.encounter_objective_tree.pack(fill="both", expand=True)
        row = ttk.Frame(objectives)
        row.pack(fill="x")
        ttk.Button(row, text="Add", command=self._encounter_add_objective).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Progress +", command=lambda: self._encounter_objective_progress(1)).pack(side="left", fill="x", expand=True)

        environment = ttk.LabelFrame(self.encounter_tray, text="Environment", padding=2)
        environment.grid(row=0, column=2, sticky="nsew", padx=2)
        self.encounter_environment_tree = ttk.Treeview(environment, columns=("Zone", "Target", "Amount", "State"), show="tree headings", height=4, style="Encounter.Treeview")
        self.encounter_environment_tree.heading("#0", text="Modifier")
        for column in ("Zone", "Target", "Amount", "State"):
            self.encounter_environment_tree.heading(column, text=column)
            self.encounter_environment_tree.column(column, width=46)
        self.encounter_environment_tree.pack(fill="both", expand=True)
        ttk.Button(environment, text="Add / Toggle", command=self._encounter_add_environment).pack(fill="x")

        history = ttk.LabelFrame(self.encounter_tray, text="History", padding=2)
        history.grid(row=0, column=3, sticky="nsew", padx=(2, 0))
        self.encounter_log_tree = ttk.Treeview(history, columns=("Round", "Type"), show="tree headings", height=4, style="Encounter.Treeview")
        self.encounter_log_tree.heading("#0", text="Event")
        self.encounter_log_tree.heading("Round", text="Round")
        self.encounter_log_tree.heading("Type", text="Type")
        self.encounter_log_tree.pack(fill="both", expand=True)

    def _toggle_encounter_library(self, force: bool | None = None) -> None:
        """Show or hide session management without hiding its reopen control."""
        expanded = (not self.encounter_library_expanded) if force is None else force
        if expanded != self.encounter_library_expanded:
            self.encounter_library_expanded = expanded
            if expanded:
                self.encounter_library_body.pack(
                    fill="x", before=self.encounter_roster_controls, pady=(0, 4))
            else:
                self.encounter_library_body.pack_forget()
        if hasattr(self, "encounter_manager_button"):
            self.encounter_manager_button.configure(
                text="Hide Manager" if self.encounter_library_expanded else "Show Manager")

    def _toggle_encounter_tray(self, force: bool | None = None) -> None:
        expanded = (not self.encounter_tray_expanded) if force is None else force
        if expanded == self.encounter_tray_expanded:
            return
        self.encounter_tray_expanded = expanded
        if expanded:
            self._encounter_pre_tray_size = (self.root.winfo_width(), self.root.winfo_height())
            self.encounter_tray.pack(fill="x", padx=8, pady=(3, 0), before=self.encounter_panes)
            self.root.update_idletasks()
            width, height = self._encounter_pre_tray_size
            available = self.root.winfo_screenheight() - self.root.winfo_y() - 35
            expanded_height = min(available, height + self.encounter_tray.winfo_reqheight() + 3)
            if expanded_height > height:
                self.root.geometry(f"{width}x{expanded_height}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        else:
            self.encounter_tray.pack_forget()
            previous = getattr(self, "_encounter_pre_tray_size", None)
            if previous and self.root.state() == "normal":
                width, height = previous
                self.root.geometry(f"{width}x{height}+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def _set_initial_encounter_sashes(self) -> None:
        if not hasattr(self, "encounter_panes") or getattr(self, "_encounter_sashes_initialized", False):
            return
        width = self.encounter_panes.winfo_width()
        if width < 1000:
            return
        try:
            left = max(300, min(410, int(width * 0.31)))
            self.encounter_panes.sashpos(0, left)
            if len(self.encounter_panes.panes()) > 2:
                center = max(310, min(400, int(width * 0.29)))
                self.encounter_panes.sashpos(1, min(width - 330, left + center))
            self._encounter_sashes_initialized = True
        except tk.TclError:
            return

    def _finalize_encounter_sashes(self) -> None:
        self._encounter_sashes_initialized = False
        self._set_initial_encounter_sashes()

    def _selected_campaign_id(self) -> str:
        selected = self.enc_campaign_tree.selection()
        return selected[0] if selected else ""

    def _selected_template_id(self) -> str:
        selected = self.enc_template_tree.selection()
        return selected[0] if selected else ""

    def _selected_session_id(self) -> str:
        selected = self.enc_session_tree.selection()
        return selected[0] if selected else ""

    def _selected_encounter_combatant(self) -> Combatant | None:
        if not self.current_encounter:
            return None
        selected = self.encounter_tree.selection()
        return next((item for item in self.current_encounter.combatants if selected and item.id == selected[0]), None)

    def _selected_encounter_objective(self) -> Objective | None:
        if not self.current_encounter:
            return None
        selected = self.encounter_tree.selection()
        return next((item for item in self.current_encounter.objectives if selected and item.id == selected[0]), None)

    def _encounter_incoming_damage(self, event=None) -> str | None:
        """Start a headless hit check, or bypass it with Shift-double-click."""
        if event is not None:
            row_id = self.encounter_tree.identify_row(event.y)
            if not row_id or not self.encounter_tree.exists(row_id):
                return None
            self.encounter_tree.selection_set(row_id)
        target = self._selected_encounter_combatant()
        marker = self._selected_encounter_objective()
        if marker:
            if marker.kind == "Marker":
                self._encounter_add_marker(marker)
            return "break"
        if not target or not self.current_encounter:
            return None
        if event is not None and event.state & 0x0001:
            return self._open_incoming_damage_prompt(target)
        return self._encounter_headless_to_hit(target)

    @staticmethod
    def _prompt_wheel_direction(event) -> int:
        delta = getattr(event, "delta", 0)
        if delta:
            return 1 if delta > 0 else -1
        return 1 if getattr(event, "num", 0) == 4 else -1

    @classmethod
    def _adjust_prompt_number(cls, variable, event, *, minimum: int | None = None, maximum: int | None = None, default: int = 0) -> str:
        try:
            current = int(variable.get())
        except (TypeError, ValueError):
            current = default
        value = current + cls._prompt_wheel_direction(event)
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        variable.set(str(value))
        return "break"

    @classmethod
    def _cycle_prompt_choice(cls, variable, values, event) -> str:
        choices = tuple(values)
        if not choices:
            return "break"
        try:
            index = choices.index(variable.get())
        except ValueError:
            index = 0
        variable.set(choices[(index + cls._prompt_wheel_direction(event)) % len(choices)])
        return "break"

    @staticmethod
    def _bind_prompt_wheel(widget, callback) -> None:
        def guarded(event):
            if str(widget.cget("state")) == "disabled":
                return "break"
            return callback(event)

        widget.bind("<MouseWheel>", guarded)
        widget.bind("<Button-4>", guarded)
        widget.bind("<Button-5>", guarded)

    def _encounter_headless_to_hit(self, target: Combatant) -> str:
        window = tk.Toplevel(self.root)
        window.title(f"Incoming hit check — {target.name}")
        window.transient(self.root)
        window.resizable(False, False)
        window.grab_set()
        defense_var = tk.StringVar(value="DODGE")
        total_var = tk.StringVar()
        target_var = tk.StringVar()
        result_var = tk.StringVar(value="Enter the attack's final to-hit total.")
        form = ttk.Frame(window, padding=12)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text=target.name, font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(form, text="Target defense").grid(row=1, column=0, sticky="w", pady=(8, 2))
        defense_box = ttk.Combobox(form, textvariable=defense_var, values=("DODGE", "PARRY"), state="readonly", width=12)
        defense_box.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(8, 2))
        ttk.Label(form, text="Final to-hit total").grid(row=2, column=0, sticky="w", pady=2)
        total_entry = ttk.Entry(form, textvariable=total_var, width=10)
        total_entry.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=2)
        ttk.Label(form, textvariable=target_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(form, textvariable=result_var, foreground="#1769aa").grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        def update_target(*_args) -> None:
            result = RulesEngine.headless_attack_result(target, 0, defense_var.get(), self.current_encounter.environment if self.current_encounter else ())
            target_var.set(f"Target: 10 + {result['modifier']} = {result['defense']}")

        def cycle_defense(event) -> str:
            result = self._cycle_prompt_choice(defense_var, ("DODGE", "PARRY"), event)
            update_target()
            return result

        def adjust_total(event) -> str:
            target_result = RulesEngine.headless_attack_result(target, 0, defense_var.get(), self.current_encounter.environment if self.current_encounter else ())
            return self._adjust_prompt_number(total_var, event, minimum=-100, maximum=300, default=int(target_result["defense"]))

        def continue_if_hit() -> None:
            try:
                attack_total = int(total_var.get())
                result = RulesEngine.headless_attack_result(target, attack_total, defense_var.get(), self.current_encounter.environment if self.current_encounter else ())
            except ValueError as exc:
                result_var.set(str(exc))
                return
            if result["hit"]:
                window.grab_release()
                window.destroy()
                self._open_incoming_damage_prompt(target)
                return
            summary = f"{attack_total} missed {target.name}'s {defense_var.get()} {result['defense']}."
            target_id = target.id
            self._encounter_mutate(
                "Incoming Attack", summary, lambda _session: None,
                actor_id=target_id, dice={"attack": result},
            )
            result_var.set("MISS — logged without applying damage.")
            continue_button.configure(state="disabled")
            defense_box.configure(state="disabled")
            total_entry.configure(state="disabled")
            close_button.configure(text="Close")

        defense_box.bind("<<ComboboxSelected>>", update_target)
        self._bind_prompt_wheel(defense_box, cycle_defense)
        self._bind_prompt_wheel(total_entry, adjust_total)
        controls = ttk.Frame(form)
        controls.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        close_button = ttk.Button(controls, text="Cancel", command=window.destroy)
        close_button.pack(side="left")
        continue_button = ttk.Button(controls, text="Continue on Hit", command=continue_if_hit, style="EncounterPrimary.TButton")
        continue_button.pack(side="left", padx=(5, 0))
        window.bind("<Return>", lambda _event: continue_if_hit() if str(continue_button.cget("state")) != "disabled" else window.destroy())
        window.bind("<Escape>", lambda _event: window.destroy())
        update_target()
        total_entry.focus_set()
        return "break"

    def _open_incoming_damage_prompt(self, target: Combatant) -> str:

        window = tk.Toplevel(self.root)
        window.title(f"Incoming attack — {target.name}")
        window.transient(self.root)
        window.resizable(False, False)
        window.grab_set()
        damage_type_var = tk.StringVar(value=next(iter(DAMAGE_TYPE_RULES)))
        rank_var = tk.StringVar(value=str(target.power_level))
        roll_var = tk.StringVar()
        interaction_var = tk.StringVar()
        result_var = tk.StringVar(value="Enter the effect rank and the target's resistance d20 roll.")
        form = ttk.Frame(window, padding=12)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text=target.name, font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(form, text="Damage type").grid(row=1, column=0, sticky="w", pady=(8, 2))
        damage_box = ttk.Combobox(form, textvariable=damage_type_var, values=tuple(DAMAGE_TYPE_RULES), state="readonly", width=20)
        damage_box.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(8, 2))
        ttk.Label(form, text="Effect rank").grid(row=2, column=0, sticky="w", pady=2)
        rank_entry = ttk.Entry(form, textvariable=rank_var, width=8)
        rank_entry.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=2)
        ttk.Label(form, text="Resistance d20").grid(row=3, column=0, sticky="w", pady=2)
        roll_entry = ttk.Entry(form, textvariable=roll_var, width=8)
        roll_entry.grid(row=3, column=1, sticky="w", padx=(10, 0), pady=2)
        ttk.Separator(form).grid(row=4, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(form, textvariable=interaction_var, wraplength=470, justify="left").grid(row=5, column=0, columnspan=2, sticky="ew")
        ttk.Label(form, textvariable=result_var, wraplength=470, justify="left", foreground="#1769aa").grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        def update_interaction(*_args) -> None:
            rule = DAMAGE_TYPE_RULES[damage_type_var.get()]
            profile = RulesEngine.damage_protection_profile(target)
            armor_behavior = str(rule["armor"])
            if armor_behavior == "arc":
                armor_behavior = "blocked" if profile["nonconductive"] else "ignored (conductive/unknown)"
            elif armor_behavior == "emp":
                armor_behavior = "blocked" if profile["hardened"] else "ignored (not hardened)"
            elif armor_behavior == "poison":
                armor_behavior = "blocked" if profile["sealed"] else "ignored (delivery assumed to bypass)"
            protection = (
                f"Shield: {'present' if profile['shield_present'] else 'none'} ({int(profile['shield_bonus']):+d} TOUGH) → {rule['shield']}.  "
                f"Armor: {'present' if profile['armor_present'] else 'none'} ({int(profile['armor_bonus']):+d} TOUGH) → {armor_behavior}."
            )
            target_kind = "Electronic target" if profile["electronic"] else "Not inferred as electronic"
            note = f"\n{rule['note']}" if rule.get("note") else ""
            interaction_var.set(f"{rule['label']} · resisted by {rule['resistance']} · {target_kind}\n{protection}{note}")

        def cycle_damage_type(event) -> str:
            result = self._cycle_prompt_choice(damage_type_var, tuple(DAMAGE_TYPE_RULES), event)
            update_interaction()
            return result

        def apply() -> None:
            try:
                rank = int(rank_var.get())
                die = int(roll_var.get())
                result = RulesEngine.incoming_damage_result(target, damage_type_var.get(), rank, die)
            except ValueError as exc:
                result_var.set(str(exc))
                return
            resistance = result["resistance"]
            destroyed = ", ".join(result["destroyed"]) or "none"
            if result["blocked_by"]:
                summary = f"Blocked entirely by {result['blocked_by']}. Destroyed protection: {destroyed}."
            else:
                suggestions = ", ".join(str(value) for value in resistance["suggestions"]) or "No effect"
                summary = (
                    f"{die} + {resistance['modifier']} = {resistance['total']} vs DC {resistance['dc']}; "
                    f"degree {resistance['degree']}. Effects: {suggestions}. Destroyed protection: {destroyed}."
                )
            target_id = target.id
            hit_conditions = [entry for entry in target.conditions if entry.active and not entry.suppressed and entry.name in {"Bleeding", "Dying"}]
            hit_rolls = self._prompt_condition_rolls(target, hit_conditions, "On-hit condition saves")
            if hit_rolls is None:
                return
            hit_tick_results: list[dict[str, object]] = []

            def operation(session: EncounterSession) -> None:
                current_target = next(entry for entry in session.combatants if entry.id == target_id)
                RulesEngine.apply_incoming_damage(current_target, result, session.round)
                hit_tick_results.extend(RulesEngine.tick_hit_conditions(current_target, session.round, hit_rolls, set(hit_rolls)))
                if current_target.defeated:
                    reveal_loot_for(session, current_target.id)

            self._encounter_mutate(
                "Incoming Damage", f"{target.name} received {damage_type_var.get()} rank {rank}: {summary}",
                operation, actor_id=target_id,
                dice={"resistance": resistance or {"die": die}, "damage_type": damage_type_var.get(), "rank": rank, "hit_condition_rolls": hit_rolls, "hit_condition_results": hit_tick_results},
            )
            result_var.set(summary)
            apply_button.configure(state="disabled")
            damage_box.configure(state="disabled")
            rank_entry.configure(state="disabled")
            roll_entry.configure(state="disabled")
            close_button.configure(text="Close")

        damage_box.bind("<<ComboboxSelected>>", update_interaction)
        self._bind_prompt_wheel(damage_box, cycle_damage_type)
        self._bind_prompt_wheel(rank_entry, lambda event: self._adjust_prompt_number(rank_var, event, minimum=0, maximum=999, default=target.power_level))
        self._bind_prompt_wheel(roll_entry, lambda event: self._adjust_prompt_number(roll_var, event, minimum=1, maximum=20, default=10))
        controls = ttk.Frame(form)
        controls.grid(row=7, column=0, columnspan=2, sticky="e", pady=(10, 0))
        close_button = ttk.Button(controls, text="Cancel", command=window.destroy)
        close_button.pack(side="left")
        apply_button = ttk.Button(controls, text="Resolve & Apply", command=apply, style="EncounterPrimary.TButton")
        apply_button.pack(side="left", padx=(5, 0))
        window.bind("<Return>", lambda _event: apply() if str(apply_button.cget("state")) != "disabled" else window.destroy())
        window.bind("<Escape>", lambda _event: window.destroy())
        update_interaction()
        roll_entry.focus_set()
        return "break"

    def _refresh_encounter_library(self, *, preserve_campaign: bool = True) -> None:
        if not hasattr(self, "enc_campaign_tree"):
            return
        campaign_id = self._selected_campaign_id() if preserve_campaign else ""
        session_id = self._selected_session_id()
        self.enc_campaign_tree.delete(*self.enc_campaign_tree.get_children())
        for campaign in self.encounter_store.list_campaigns():
            self.enc_campaign_tree.insert("", "end", iid=campaign.id, text=campaign.name, values=(", ".join(campaign.tags),))
        if campaign_id and self.enc_campaign_tree.exists(campaign_id):
            self.enc_campaign_tree.selection_set(campaign_id)
        elif self.enc_campaign_tree.get_children():
            campaign_id = self.enc_campaign_tree.get_children()[0]
            self.enc_campaign_tree.selection_set(campaign_id)
        else:
            campaign_id = ""
        self._refresh_encounter_campaign_contents(campaign_id, session_id)

    def _on_encounter_campaign_selected(self, _event=None) -> None:
        """Refresh dependent lists without rebuilding and reselecting campaigns."""
        self._refresh_encounter_campaign_contents(
            self._selected_campaign_id(),
            self._selected_session_id(),
        )

    def _refresh_encounter_campaign_contents(self, campaign_id: str, session_id: str = "") -> None:
        self.enc_template_tree.delete(*self.enc_template_tree.get_children())
        for template in self.encounter_store.list_templates(campaign_id=campaign_id or None):
            self.enc_template_tree.insert("", "end", iid=template.id, text=template.name, values=(template.difficulty,))
        self.enc_session_tree.delete(*self.enc_session_tree.get_children())
        for session in self.encounter_store.list_sessions(campaign_id=campaign_id or None):
            self.enc_session_tree.insert("", "end", iid=session.id, text=session.name, values=(session.status, session.round))
        if session_id and self.enc_session_tree.exists(session_id):
            self.enc_session_tree.selection_set(session_id)

    def _new_campaign(self) -> None:
        name = simpledialog.askstring("New campaign", "Campaign name:", parent=self.root)
        if not name:
            return
        campaign = Campaign(name=name.strip())
        self.encounter_store.save_campaign(campaign)
        self._refresh_encounter_library(preserve_campaign=False)
        if self.enc_campaign_tree.exists(campaign.id):
            self.enc_campaign_tree.selection_set(campaign.id)

    def _edit_campaign(self) -> None:
        campaign = self.encounter_store.get_campaign(self._selected_campaign_id())
        if not campaign:
            return
        name = simpledialog.askstring("Edit campaign", "Campaign name:", initialvalue=campaign.name, parent=self.root)
        if not name:
            return
        tags = simpledialog.askstring("Campaign tags", "Comma-separated tags:", initialvalue=", ".join(campaign.tags), parent=self.root)
        campaign.name = name.strip()
        campaign.tags = [item.strip() for item in (tags or "").split(",") if item.strip()]
        campaign.updated_at = datetime.now().astimezone().isoformat()
        self.encounter_store.save_campaign(campaign)
        self._refresh_encounter_library()

    def _delete_campaign(self) -> None:
        campaign_id = self._selected_campaign_id()
        if campaign_id and messagebox.askyesno("Delete campaign?", "Delete the campaign record? Templates and sessions will become unfiled."):
            self.encounter_store.delete_campaign(campaign_id)
            self._refresh_encounter_library(preserve_campaign=False)

    def _edit_campaign_party(self) -> None:
        campaign = self.encounter_store.get_campaign(self._selected_campaign_id())
        characters = self.character_store.list()
        if not campaign or not characters:
            return
        window = tk.Toplevel(self.root)
        window.title(f"{campaign.name} party roster")
        tree = ttk.Treeview(window, columns=("PL", "Race"), show="tree headings", selectmode="extended")
        tree.heading("#0", text="Character")
        tree.heading("PL", text="PL")
        tree.heading("Race", text="Race")
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        for character in characters:
            tree.insert("", "end", iid=character.id, text=character.name, values=(character.power_level, character.race))
        tree.selection_set([item_id for item_id in campaign.party_character_ids if tree.exists(item_id)])

        def save() -> None:
            campaign.party_character_ids = list(tree.selection())
            campaign.updated_at = datetime.now().astimezone().isoformat()
            self.encounter_store.save_campaign(campaign)
            window.destroy()

        ttk.Button(window, text="Save party", command=save).pack(anchor="e", padx=8, pady=(0, 8))

    def _new_encounter_template(self) -> None:
        name = simpledialog.askstring("New encounter template", "Template name:", parent=self.root)
        if not name:
            return
        template = EncounterTemplate(campaign_id=self._selected_campaign_id(), name=name.strip())
        self.encounter_store.save_template(template)
        self._refresh_encounter_library()
        if self.enc_template_tree.exists(template.id):
            self.enc_template_tree.selection_set(template.id)

    def _start_encounter_template(self) -> None:
        template = self.encounter_store.get_template(self._selected_template_id())
        if not template:
            name = simpledialog.askstring("New encounter", "Encounter name:", parent=self.root)
            if not name:
                return
            template = EncounterTemplate(campaign_id=self._selected_campaign_id(), name=name.strip())
        session = EncounterSession.from_template(template)
        if template.recipe_slots:
            if not self.snapshot:
                messagebox.showwarning("Catalog unavailable", "Wait for the live catalog before building this recipe.")
                return
            defaults = self._encounter_generator_defaults()
            if defaults is None:
                return
            session.combatants = self.encounter_builder.build_recipe(template, self.snapshot, {item.id: item for item in self.character_store.list()}, base_settings=defaults)
            session.loot.clear()
            stage_loot(session)
        self.encounter_store.save_session(session)
        self._set_current_encounter(session)
        self._refresh_encounter_library()

    def _delete_encounter_template(self) -> None:
        template_id = self._selected_template_id()
        if template_id and messagebox.askyesno("Delete template?", "Delete this reusable encounter template? Existing sessions remain intact."):
            self.encounter_store.delete_template(template_id)
            self._refresh_encounter_library()

    def _save_session_as_template(self) -> None:
        if not self.current_encounter:
            return
        name = simpledialog.askstring("Save as template", "Template name:", initialvalue=self.current_encounter.name, parent=self.root)
        if not name:
            return
        template = EncounterTemplate(
            campaign_id=self.current_encounter.campaign_id or self._selected_campaign_id(), name=name.strip(),
            combatants=copy.deepcopy(self.current_encounter.combatants), objectives=copy.deepcopy(self.current_encounter.objectives),
            environment=copy.deepcopy(self.current_encounter.environment), notes=self.current_encounter.notes,
        )
        for source in self.current_encounter.combatants:
            character = source.character()
            source_type = "Saved" if source.source_character_id else "Ad Hoc" if source.kind == "Ad Hoc" else "Generated"
            settings = {"power_level": source.power_level}
            if character:
                settings.update({"role": character.role, "race": character.race, "budget": character.budget})
            template.recipe_slots.append(RecipeSlot(source_type=source_type, source_character_id=source.source_character_id, name=source.name, settings=settings, faction=source.faction, minion=source.minion, initiative_group=source.initiative_group, locked=True))
        for item in template.combatants:
            item.initiative = None
            item.bruises = 0
            for condition in item.conditions:
                RulesEngine.deactivate_condition(item, condition)
            item.conditions.clear()
            item.defeated = False
        self.encounter_store.save_template(template)
        self._refresh_encounter_library()

    def _load_encounter_session(self) -> None:
        session = self.encounter_store.get_session(self._selected_session_id())
        if session:
            self._set_current_encounter(session)

    def _delete_encounter_session(self) -> None:
        session_id = self._selected_session_id()
        if session_id and messagebox.askyesno("Delete encounter?", "Permanently delete this session and its event history?"):
            self.encounter_store.delete_session(session_id)
            if self.current_encounter and self.current_encounter.id == session_id:
                self.current_encounter = None
                self.encounter_controller = None
            self._refresh_encounter_library()
            self._refresh_encounter_view()

    def _set_current_encounter(self, session: EncounterSession) -> None:
        self.current_encounter = session
        self.encounter_controller = EncounterController(session, self.encounter_store)
        if hasattr(self, "encounter_library_expanded"):
            self._toggle_encounter_library(False)
        self._refresh_encounter_view(preserve_selection=False)

    def _encounter_mutate(self, event_type: str, summary: str, operation, *, actor_id: str = "", dice=None) -> None:
        if not self.encounter_controller:
            return
        self._clear_encounter_resolution_preview()
        self.encounter_controller.mutate(event_type, summary, operation, actor_id=actor_id, dice=dice)
        self.current_encounter = self.encounter_controller.session
        self._refresh_encounter_view()

    def _refresh_encounter_view(self, *, preserve_selection: bool = True) -> None:
        if not hasattr(self, "encounter_tree"):
            return
        selected = self.encounter_tree.selection() if preserve_selection else ()
        roster_scroll = self.encounter_tree.yview()[0] if preserve_selection and self.encounter_tree.get_children() else 0.0
        self.encounter_tree.delete(*self.encounter_tree.get_children())
        session = self.current_encounter
        if not session:
            self.encounter_title_var.set("No active encounter")
            self.encounter_round_var.set("Round —")
            self._refresh_encounter_inspector()
            return
        self.encounter_title_var.set(session.name)
        current = session.current_entity()
        self.encounter_round_var.set(f"Round {session.round} — {current.name if current else 'No turn'}")
        needle = self.encounter_filter_var.get().strip().casefold()
        for item in session.ordered_turn_entities():
            if isinstance(item, Combatant):
                faction = item.faction
                bruises: int | str = item.bruises
                state = "Defeated" if item.defeated else ", ".join(sorted(name.title() for name in RulesEngine.active_conditions(item))) or "Ready"
                if not item.defeated and any(
                    ammo is not None and ammo.empty
                    for action in item.actions for ammo in (weapon_ammo(item, action),)
                ):
                    state += " · Out of ammo"
            else:
                faction = item.kind
                bruises = "—"
                state = item.state + (f" · {item.timer} rnd" if item.timer is not None else "")
            if needle and needle not in f"{item.name} {faction} {state}".casefold():
                continue
            is_current = bool(current and item.id == current.id)
            if isinstance(item, Combatant) and not item.minion:
                tags = ("heroic-current-turn",) if is_current else ("heroic",)
            else:
                tags = ("current-turn",) if is_current else ()
            self.encounter_tree.insert("", "end", iid=item.id, values=(item.initiative if item.initiative is not None else "—", item.name, faction, bruises, state), tags=tags)
        if selected and self.encounter_tree.exists(selected[0]):
            self.encounter_tree.selection_set(selected[0])
        elif current and self.encounter_tree.exists(current.id):
            self.encounter_tree.selection_set(current.id)
        if self.encounter_tree.get_children():
            self.encounter_tree.yview_moveto(roster_scroll)
        self._refresh_encounter_inspector()
        self._refresh_encounter_history()
        self._refresh_encounter_library()

    @staticmethod
    def _capture_tree_state(tree: ttk.Treeview) -> tuple[tuple[str, ...], float]:
        return tree.selection(), tree.yview()[0] if tree.get_children() else 0.0

    @staticmethod
    def _restore_tree_state(tree: ttk.Treeview, state: tuple[tuple[str, ...], float]) -> None:
        selection, scroll = state
        valid = [entry for entry in selection if tree.exists(entry)]
        if valid:
            tree.selection_set(valid)
        if tree.get_children():
            tree.yview_moveto(scroll)

    def _encounter_sheet_text(self, item: Combatant | Objective | None, session: EncounterSession | None) -> tuple[str, list[tuple[str, str, int, int]]]:
        if not item or not session:
            return "Select a combatant from the initiative roster.", []
        if isinstance(item, Objective):
            timer = f"{item.timer} round(s) remaining" if item.timer is not None else "No timer"
            return (
                f"{item.name}\n{item.kind.upper()} · Initiative {item.initiative if item.initiative is not None else '—'}\n"
                f"State: {item.state} · {timer}\n\nThis turn marker has no character stats. "
                "Double-click it in the roster to edit its label or initiative.\n\n"
                f"NOTES\n{item.notes or 'None'}\n",
                [],
            )
        parts: list[str] = []
        tags: list[tuple[str, str, int, int]] = []
        length = 0

        def add(text: str) -> None:
            nonlocal length
            parts.append(text)
            length += len(text)

        def add_tagged(kind: str, identity: str, label: str) -> None:
            start = length
            add(label)
            tags.append((kind, identity, start, length))

        abilities, defenses, speed = RulesEngine.stats(item, session.environment)
        source = "Source changed — refresh available" if item.source_character_id and self.encounter_store.source_changed(item, self.character_store.get(item.source_character_id)) else "Snapshot current"
        add_tagged("combatant", item.id, item.name)
        add(f" — {item.faction}\nPL {item.power_level}  {'Minion' if item.minion else 'Heroic'}  Zone: {item.zone}  Initiative: {item.initiative if item.initiative is not None else '—'}\n")
        add(f"{source}\nRound {session.round}  Bruises: {item.bruises}  Check penalty: {RulesEngine.check_penalty(item):+d}  Speed: {format_speed(speed)}\n")
        add("Abilities: " + " ".join(f"{key}:{value}" for key, value in abilities.items()) + "\n")
        toughness = RulesEngine.toughness_breakdown(item, defenses["TOUGH"])
        add("Defenses: " + format_defenses(defenses, toughness) + "\n")
        used = [name for name, active in (("Delayed", item.delayed), ("Readied", item.readied), ("Holding", item.holding), ("Skipped", item.skipped), ("Standard used", item.standard_used), ("Move used", item.move_used), ("Reaction used", item.reaction_used), ("Defeated", item.defeated)) if active]
        add(f"Turn state: {', '.join(used) or 'Ready'}\n\n")
        metadata_expanded = item.id in getattr(self, "encounter_metadata_expanded", set())
        metadata_start = length
        add(f"Character MetaData [{'Hide' if metadata_expanded else 'Show'}]\n")
        tags.append(("metadata", item.id, metadata_start, length))

        add("ACTIONS\n")
        if item.actions:
            for action in item.actions:
                attack = action.fixed_attack if action.fixed_attack is not None else action.attack_modifier
                available, required = RulesEngine.action_resource_status(item, action)
                resource = f"; {available}/{required} {action.resource_name}" if required else ""
                add("• ")
                add_tagged("action", action.id, action.name)
                add(f" — {action.action_type} {action.effect_type}; attack {attack:+d} vs {action.target_defense}; DC {action.resistance_dc} {action.resistance_defense}{resource}\n")
        else:
            add("None\n")
        add("\nCONDITIONS\n")
        if item.conditions:
            for condition in item.conditions:
                add("• ")
                add_tagged("condition", condition.id, condition.name)
                add(f" — {condition.duration_mode}; {condition.source or 'no source'}; {'active' if condition.active else 'expired'}\n")
        else:
            add("None\n")
        character = item.character()
        add("\nGEAR\n")
        if character:
            expanded = self.encounter_expanded_items.setdefault(item.id, set())
            valid_ids = {gear.id for gear in character.equipment}
            expanded.intersection_update(valid_ids)
            for section in SECTIONS:
                add(f"{section}:\n")
                gear_items = [gear for gear in character.equipment if gear.category == section]
                if not gear_items:
                    add("- None\n")
                for gear in gear_items:
                    attack = f"{character.to_hit_modifier(gear):+d} " if gear.category == "Weapons" else ""
                    quantity = f" ×{gear.quantity}" if gear.quantity != 1 else ""
                    add("- " + attack)
                    add_tagged("gear", gear.id, gear.name)
                    add(f"{quantity} [{gear.manufacturer}]\n")
                    if gear.id in expanded:
                        for label, value in gear.fields.items():
                            if label != "Manufacturer" and value.strip().casefold() not in {"", "-", "none"}:
                                add(f"    {label}: {value}\n")
                        add(f"    Adjusted Price: {gear.adjusted_price:g}\n")
                        if gear.capacity:
                            add(f"    Capacity: {gear.capacity:g}\n")
                        if gear.category == "Genemods":
                            add(f"    Rolled Corruption: {gear.corruption}\n")
        else:
            add("No gear is available for this ad hoc combatant.\n")
        if not metadata_expanded:
            text = "".join(parts)
            tags.extend((f"toughness-{kind}", kind, start, end) for kind, start, end in toughness_token_spans(text))
            return text, tags
        add("\nRESOURCES\n")
        if item.resources:
            for resource in item.resources:
                add("• ")
                add_tagged("resource", resource.id, resource.name)
                add(f": {resource.current}/{resource.maximum if resource.maximum is not None else '—'}\n")
        else:
            add("None\n")
        add("\nRUNTIME MODIFIERS\n")
        if item.modifiers:
            for modifier in item.modifiers:
                add("• ")
                add_tagged("modifier", modifier.id, modifier.source or modifier.target)
                add(f": {modifier.target} {modifier.amount:+d} ({'active' if modifier.active else 'off'})\n")
        else:
            add("None\n")
        add(f"\nNOTES\n{item.notes or 'None'}\n")

        character = item.character()
        if character:
            add("\nCHARACTER SNAPSHOT\n")
            character_start = length
            expanded = self.encounter_expanded_items.setdefault(item.id, set())
            valid_ids = {gear.id for gear in character.equipment}
            expanded.intersection_update(valid_ids)
            snapshot_text = character_text(character, audit=True, expanded_item_ids=expanded, include_equipment=False)
            add(snapshot_text)
            search_from = 0
            displayed_gear = [gear for section in SECTIONS for gear in character.equipment if gear.category == section]
            for gear in displayed_gear:
                pattern = re.compile(
                    rf"(?m)^- (?:[+-]\d+ )?(?P<name>{re.escape(gear.name)})(?: ×\d+)? \["
                )
                match = pattern.search(snapshot_text, search_from)
                if not match:
                    continue
                found = match.start("name")
                start = character_start + found
                tags.append(("gear", gear.id, start, start + len(gear.name)))
                search_from = found + len(gear.name)
        else:
            add("\nAD HOC COMBATANT\nNo frozen character snapshot or equipment receipt is available.\n")
        text = "".join(parts)
        tags.extend((f"toughness-{kind}", kind, start, end) for kind, start, end in toughness_token_spans(text))
        return text, tags

    def _bind_encounter_sheet_tags(self, tags: list[tuple[str, str, int, int]]) -> None:
        colors = {
            "combatant": "#d97706", "action": "#1769aa", "condition": "#b42318",
            "resource": "#2e7d32", "modifier": "#7b1fa2", "gear": "#1769aa", "metadata": "#1769aa",
            **{f"toughness-{kind}": color for kind, color in TOUGHNESS_COLORS.items()},
        }
        interactive_kinds = {"combatant", "action", "condition", "resource", "modifier", "gear"}
        self._encounter_sheet_tag_names = {}
        self._encounter_sheet_tag_targets = {}
        for index, (kind, identity, start, end) in enumerate(tags):
            tag = f"encounter-sheet-{kind}-{index}"
            self._encounter_text_tags.append(tag)
            if kind in interactive_kinds:
                self._encounter_sheet_tag_names[(kind, identity)] = tag
                self._encounter_sheet_tag_targets[tag] = (kind, identity)
            self.encounter_stats_text.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")
            self.encounter_stats_text.tag_configure(tag, foreground=colors[kind], underline=kind in interactive_kinds or kind == "metadata")
            if kind in interactive_kinds:
                self.encounter_stats_text.tag_bind(tag, "<Enter>", lambda _event: self.encounter_stats_text.configure(cursor="hand2"))
                self.encounter_stats_text.tag_bind(tag, "<Leave>", lambda _event: self.encounter_stats_text.configure(cursor=""))
            if kind == "gear":
                self.encounter_stats_text.tag_bind(tag, "<Button-1>", lambda _event, item_id=identity: self._toggle_encounter_item_details(item_id))
            elif kind == "action":
                self.encounter_stats_text.tag_bind(tag, "<ButtonPress-1>", lambda event, action_id=identity: self._encounter_sheet_action_drag_start(event, action_id))
            elif kind == "metadata":
                self.encounter_stats_text.tag_bind(tag, "<Enter>", lambda _event: self.encounter_stats_text.configure(cursor="hand2"))
                self.encounter_stats_text.tag_bind(tag, "<Leave>", lambda _event: self.encounter_stats_text.configure(cursor=""))
                self.encounter_stats_text.tag_bind(tag, "<Button-1>", lambda _event, item_id=identity: self._toggle_encounter_metadata(item_id))

    def _toggle_encounter_item_details(self, item_id: str) -> str:
        combatant = self._selected_encounter_combatant()
        if not combatant:
            return "break"
        expanded = self.encounter_expanded_items.setdefault(combatant.id, set())
        expanded.remove(item_id) if item_id in expanded else expanded.add(item_id)
        self._refresh_encounter_inspector()
        return "break"

    def _toggle_encounter_metadata(self, item_id: str) -> str:
        expanded = getattr(self, "encounter_metadata_expanded", set())
        expanded.remove(item_id) if item_id in expanded else expanded.add(item_id)
        self.encounter_metadata_expanded = expanded
        self._refresh_encounter_inspector()
        return "break"

    def _select_encounter_sheet_action(self, action_id: str) -> str:
        if self.encounter_action_tree.exists(action_id):
            self.encounter_action_tree.selection_set(action_id)
            self._clear_encounter_resolution_preview()
            self.encounter_status_var.set("Action selected; choose a target and preview it below the character sheet.")
            item = self._selected_encounter_combatant()
            self._refresh_encounter_action_picker(item)
        return "break"

    @staticmethod
    def _encounter_action_source_item_id(action: ActionCard) -> str:
        """Return the snapshot equipment id behind a derived action."""

        source_kind, separator, source_id = action.source.partition(":")
        return source_id if separator and source_kind in {"Gear", "Bionic"} else ""

    def _encounter_pick_action(self, _event=None) -> None:
        chosen = self.encounter_action_picker_var.get()
        action_id = getattr(self, "_encounter_action_picker_ids", {}).get(chosen)
        if action_id:
            self._select_encounter_sheet_action(action_id)

    def _refresh_encounter_action_picker(self, item: Combatant | None) -> None:
        selection = self.encounter_action_tree.selection()
        current_id = selection[0] if selection else None
        mapping: dict[str, str] = {}
        for index, action in enumerate(item.actions if item else [], start=1):
            label = f"{index}. {action.name} · {action.effect_type}"
            mapping[label] = action.id
        self._encounter_action_picker_ids = mapping
        self.encounter_action_picker["values"] = tuple(mapping)
        active = next((label for label, identity in mapping.items() if identity == current_id), "")
        self.encounter_action_picker_var.set(active)

    def _expand_encounter_action_source(self, action_id: str) -> bool:
        """Expand and reveal the equipment entry that generated an action."""

        combatant = self._selected_encounter_combatant()
        action = next((entry for entry in combatant.actions if entry.id == action_id), None) if combatant else None
        source_id = self._encounter_action_source_item_id(action) if action else ""
        character = combatant.character() if combatant else None
        source = next((item for item in character.equipment if item.id == source_id), None) if character and source_id else None
        if not combatant or not source:
            return False

        self.encounter_expanded_items.setdefault(combatant.id, set()).add(source.id)
        self._refresh_encounter_inspector()
        if self.encounter_gear_tree.exists(source.id):
            self.encounter_gear_tree.selection_set(source.id)
            self.encounter_gear_tree.see(source.id)
        tag = getattr(self, "_encounter_sheet_tag_names", {}).get(("gear", source.id))
        if tag:
            self.encounter_stats_text.see(f"{tag}.first")
        self.encounter_status_var.set(f"Expanded {source.name}, the source of {action.name}.")
        return True

    def _encounter_action_clicked(self, event) -> str | None:
        action_id = self.encounter_action_tree.identify_row(event.y)
        if not action_id or not self.encounter_action_tree.exists(action_id):
            return None
        self.encounter_action_tree.selection_set(action_id)
        self._clear_encounter_resolution_preview()
        if event.state & 0x0001:
            self._expand_encounter_action_source(action_id)
        return "break"

    def _encounter_action_drag_start(self, event) -> str | None:
        action_id = self.encounter_action_tree.identify_row(event.y)
        actor = self._selected_encounter_combatant() if getattr(self, "current_encounter", None) else None
        self._encounter_action_drag = {
            "action_id": action_id, "x": event.x_root, "y": event.y_root, "dragged": False, "source": "tree",
            "actor_id": actor.id if actor else "",
        } if action_id and actor else None
        if action_id:
            self._suspend_encounter_action_drag_bindings(self.encounter_action_tree)
            self.encounter_action_tree.selection_set(action_id)
            self._clear_encounter_resolution_preview()
            return "break"
        return None

    def _encounter_sheet_action_drag_start(self, event, action_id: str) -> str:
        self.encounter_stats_text.tag_remove("sel", "1.0", "end")
        actor = self._selected_encounter_combatant() if getattr(self, "current_encounter", None) else None
        self._encounter_action_drag = {
            "action_id": action_id, "x": event.x_root, "y": event.y_root, "dragged": False, "source": "sheet",
            "state": event.state,
            "actor_id": actor.id if actor else "",
        }
        self._suspend_encounter_action_drag_bindings(self.encounter_stats_text)
        return "break"

    def _suspend_encounter_action_drag_bindings(self, widget) -> None:
        drag = getattr(self, "_encounter_action_drag", None)
        if not drag:
            return
        original = widget.bindtags()
        widget_class = widget.winfo_class()
        drag["binding_widget"] = widget
        drag["binding_tags"] = original
        widget.bindtags(tuple(tag for tag in original if tag != widget_class))

    @staticmethod
    def _restore_encounter_action_drag_bindings(drag) -> None:
        widget = drag.get("binding_widget") if drag else None
        tags = drag.get("binding_tags") if drag else None
        if widget is not None and tags:
            try:
                widget.bindtags(tags)
            except tk.TclError:
                pass

    def _show_encounter_action_drag_ghost(self, action_id: str, x_root: int, y_root: int) -> None:
        self._destroy_encounter_action_drag_ghost()
        combatant = self._selected_encounter_combatant()
        action = next((entry for entry in combatant.actions if entry.id == action_id), None) if combatant else None
        if not action:
            return
        ghost = tk.Label(
            self.root, text=f"  {action.name}  ", background="#dbeafe", foreground="#123b63",
            relief="solid", borderwidth=1, font=("TkDefaultFont", 9, "bold"),
        )
        self._encounter_action_drag_ghost = ghost
        self._move_encounter_action_drag_ghost(x_root, y_root)
        ghost.lift()

    def _move_encounter_action_drag_ghost(self, x_root: int, y_root: int) -> None:
        ghost = getattr(self, "_encounter_action_drag_ghost", None)
        if ghost and ghost.winfo_exists():
            x = x_root - self.root.winfo_rootx() + 14
            y = y_root - self.root.winfo_rooty() + 12
            ghost.place_configure(x=x, y=y)
            ghost.lift()

    def _destroy_encounter_action_drag_ghost(self) -> None:
        ghost = getattr(self, "_encounter_action_drag_ghost", None)
        self._encounter_action_drag_ghost = None
        if ghost:
            try:
                ghost.destroy()
            except tk.TclError:
                pass

    def _encounter_action_drag_motion(self, event) -> str | None:
        drag = getattr(self, "_encounter_action_drag", None)
        if not drag:
            return None
        if drag["source"] == "sheet":
            self.encounter_stats_text.tag_remove("sel", "1.0", "end")
        if not drag["dragged"] and abs(event.x_root - drag["x"]) + abs(event.y_root - drag["y"]) >= 8:
            drag["dragged"] = True
            self.encounter_action_tree.configure(cursor="hand2")
            self._show_encounter_action_drag_ghost(drag["action_id"], event.x_root, event.y_root)
        elif drag["dragged"]:
            self._move_encounter_action_drag_ghost(event.x_root, event.y_root)
        # Suppress Text/Treeview class bindings while an action gesture is active.
        return "break"

    def _encounter_action_drag_release(self, event) -> str | None:
        drag = getattr(self, "_encounter_action_drag", None)
        if not drag:
            return None
        self._encounter_action_drag = None
        self._restore_encounter_action_drag_bindings(drag)
        self._destroy_encounter_action_drag_ghost()
        self.encounter_action_tree.configure(cursor="")
        if not drag["dragged"]:
            if drag["source"] == "sheet":
                self._select_encounter_sheet_action(drag["action_id"])
                if drag.get("state", 0) & 0x0001:
                    self._expand_encounter_action_source(drag["action_id"])
                return "break"
            return self._encounter_action_clicked(event)
        target_x = event.x_root - self.encounter_tree.winfo_rootx()
        target_y = event.y_root - self.encounter_tree.winfo_rooty()
        inside = 0 <= target_x < self.encounter_tree.winfo_width() and 0 <= target_y < self.encounter_tree.winfo_height()
        target_id = self.encounter_tree.identify_row(target_y) if inside else ""
        if target_id:
            self._encounter_use_action_on_target(drag["action_id"], target_id, drag.get("actor_id", ""))
        return "break"

    def _cancel_encounter_action_drag(self, _event=None) -> str:
        drag = getattr(self, "_encounter_action_drag", None)
        self._encounter_action_drag = None
        self._restore_encounter_action_drag_bindings(drag)
        self._destroy_encounter_action_drag_ghost()
        if hasattr(self, "encounter_action_tree"):
            self.encounter_action_tree.configure(cursor="")
        return "break"

    def _encounter_action_double_clicked(self, event) -> str:
        action_id = self.encounter_action_tree.identify_row(event.y)
        if action_id:
            self._encounter_consume_action_resource(action_id)
        return "break"

    def _popup_encounter_menu(self, event, entries: list[tuple[str, object] | tuple[str, object, bool] | None]) -> str:
        menu = tk.Menu(self.root, tearoff=False)

        def add_command(label: str, command, keep_open: bool = False) -> None:
            if not keep_open:
                menu.add_command(label=label, command=command)
                return

            def run_and_reopen() -> None:
                command()
                # Let encounter refreshes finish, then restore repeatable controls
                # at the same screen position instead of making the user right-click again.
                menu.after_idle(lambda: menu.post(event.x_root, event.y_root) if menu.winfo_exists() else None)

            menu.add_command(label=label, command=run_and_reopen)

        for entry in entries:
            if entry is None:
                menu.add_separator()
            else:
                label, command, *options = entry
                add_command(label, command, bool(options and options[0]))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _encounter_character_menu_entries(self, item: Combatant, *, include_roster_actions: bool = False):
        entries = [("Incoming attack…", lambda: self._encounter_headless_to_hit(item))]
        if include_roster_actions:
            entries.append(("Set initiative", self._encounter_set_initiative))
        entries.extend([
            ("Bruise +", lambda: self._encounter_bruise(1), True),
            ("Bruise -", lambda: self._encounter_bruise(-1), True),
            ("Add condition…", self._encounter_add_condition_dialog),
            ("New action…", self._encounter_new_action),
            ("Add resource…", self._encounter_add_resource),
            ("Add gear…", self._encounter_add_gear),
            None,
            ("Toggle defeated", self._encounter_toggle_defeated, True),
            ("Toggle delayed", lambda: self._toggle_encounter_flag("delayed"), True),
            ("Toggle readied", lambda: self._toggle_encounter_flag("readied"), True),
            ("Toggle holding", lambda: self._toggle_encounter_flag("holding"), True),
            ("Toggle skipped", lambda: self._toggle_encounter_flag("skipped"), True),
            ("Toggle standard used", lambda: self._toggle_encounter_flag("standard_used"), True),
            ("Toggle move used", lambda: self._toggle_encounter_flag("move_used"), True),
            ("Toggle reaction used", lambda: self._toggle_encounter_flag("reaction_used"), True),
            None,
            ("Refresh source", self._encounter_refresh_source),
            ("Publish reusable actions", self._encounter_publish_actions),
            ("Edit notes…", self._encounter_edit_notes),
            ("Print combatant", self._print_encounter_combatant),
        ])
        if include_roster_actions:
            entries.extend((None, ("Remove", self._encounter_remove_selected)))
        return entries

    def _encounter_sheet_context_menu(self, event) -> str:
        index = self.encounter_stats_text.index(f"@{event.x},{event.y}")
        targets = getattr(self, "_encounter_sheet_tag_targets", {})
        tagged = next((targets[tag] for tag in self.encounter_stats_text.tag_names(index) if tag in targets), None)
        if tagged:
            return self._encounter_tag_context_menu(event, *tagged)
        item = self._selected_encounter_combatant()
        if not item:
            return "break"
        return self._popup_encounter_menu(event, self._encounter_character_menu_entries(item))

    def _encounter_tag_context_menu(self, event, kind: str, identity: str) -> str:
        item = self._selected_encounter_combatant()
        if not item:
            return "break"
        if kind == "combatant":
            return self._encounter_sheet_context_menu(event)
        if kind == "action":
            self._select_encounter_sheet_action(identity)
            action = next((entry for entry in item.actions if entry.id == identity), None)
            first = ("Use / log special action", lambda: self._encounter_use_special_action(identity)) if action and action.effect_type == "Special" else ("Resolve / preview", self._encounter_preview_action)
            entries = [first, ("Print action", self._print_encounter_action), ("Delete action", self._encounter_delete_action), ("Publish reusable actions", self._encounter_publish_actions)]
        elif kind == "condition":
            if self.encounter_condition_tree.exists(identity):
                self.encounter_condition_tree.selection_set(identity)
            entries = [("Force tick", self._encounter_force_condition_tick), ("Recover", self._encounter_recover_condition), ("Remove", self._encounter_remove_condition), ("Print reminder", self._print_encounter_condition)]
        elif kind == "resource":
            if self.encounter_resource_tree.exists(identity):
                self.encounter_resource_tree.selection_set(identity)
            entries = [("Increase", lambda: self._encounter_resource(1)), ("Decrease", lambda: self._encounter_resource(-1))]
        elif kind == "modifier":
            entries = [("Toggle modifier", lambda: self._encounter_toggle_modifier(identity))]
        else:
            if self.encounter_gear_tree.exists(identity):
                self.encounter_gear_tree.selection_set(identity)
            action = next((action for action in item.actions if action.source == f"Gear:{identity}"), None)
            entries = [("Collapse / expand details", lambda: self._toggle_encounter_item_details(identity))]
            if action:
                entries.append(("Resolve weapon action", lambda action_id=action.id: (self._select_encounter_sheet_action(action_id), self._encounter_preview_action())))
            entries.extend((("Remove gear", self._encounter_remove_gear), ("Print item", lambda: self._print_encounter_gear(identity))))
        return self._popup_encounter_menu(event, entries)

    def _refresh_encounter_inspector(self) -> None:
        item = self._selected_encounter_combatant()
        selected_entity: Combatant | Objective | None = item or self._selected_encounter_objective()
        session = self.current_encounter
        actor_id = selected_entity.id if selected_entity else ""
        actor_changed = actor_id != getattr(self, "_encounter_inspected_id", "")
        if actor_changed:
            self._encounter_inspected_id = actor_id
            self._clear_encounter_resolution_preview()
        state_names = (
            "encounter_action_tree", "encounter_condition_tree", "encounter_gear_tree", "encounter_resource_tree",
            "encounter_loot_tree", "encounter_objective_tree", "encounter_environment_tree",
        )
        tree_states = {name: self._capture_tree_state(getattr(self, name)) for name in state_names}
        sheet_scroll = self.encounter_stats_text.yview()[0] if not actor_changed else 0.0
        text, sheet_tags = self._encounter_sheet_text(selected_entity, session)
        self.encounter_stats_text.configure(state="normal")
        for tag in self._encounter_text_tags:
            self.encounter_stats_text.tag_delete(tag)
        self._encounter_text_tags.clear()
        self._encounter_sheet_tag_names.clear()
        self.encounter_stats_text.delete("1.0", "end")
        self.encounter_stats_text.insert("1.0", text)
        self._bind_encounter_sheet_tags(sheet_tags)
        self.encounter_stats_text.configure(state="disabled")
        self.encounter_stats_text.yview_moveto(sheet_scroll)
        self.encounter_action_tree.delete(*self.encounter_action_tree.get_children())
        self.encounter_condition_tree.delete(*self.encounter_condition_tree.get_children())
        self.encounter_gear_tree.delete(*self.encounter_gear_tree.get_children())
        self.encounter_resource_tree.delete(*self.encounter_resource_tree.get_children())
        notes_has_focus = not actor_changed and self.root.focus_get() is self.encounter_notes_text
        if not notes_has_focus:
            self.encounter_notes_text.delete("1.0", "end")
        if item:
            for action in item.actions:
                attack = action.fixed_attack if action.fixed_attack is not None else action.attack_modifier
                available, required = RulesEngine.action_resource_status(item, action)
                tags = ("insufficient-resource",) if required and available < required else ()
                ammo = weapon_ammo(item, action)
                ammo_name = ammo.name if ammo else (action.resource_name or "—")
                ammo_count = (
                    f"{ammo.available} ({ammo.per_attack}/shot)" if ammo and ammo.tracked
                    else f"{ammo.available} (untracked)" if ammo
                    else "—" if not action.resource_cost
                    else f"{available} ({required}/use)"
                )
                self.encounter_action_tree.insert(
                    "", "end", iid=action.id, text=action.name,
                    values=(f"{attack:+d} vs {action.target_defense.title()}", action.resistance_dc,
                            ammo_name, ammo_count),
                    tags=tags)
            for condition in item.conditions:
                state = condition.duration_mode if condition.active else "Expired"
                if condition.active and condition.name in {"Burning", "Poisoned"}:
                    state = f"{condition.rounds_remaining} ticks · rank {condition.original_rank // 2}"
                    if condition.name == "Poisoned":
                        stage = ("None", "Hindered", "Immobile", "Dying")[max(0, min(3, condition.stage))]
                        state += f" · {stage} · DC {10 + condition.saves_made}"
                elif condition.active and condition.name in {"Bleeding", "Dying"}:
                    state = f"Next DC {10 + condition.saves_made}"
                elif condition.active and condition.name == "Diseased":
                    state = f"{len(condition.modifier_ids)} attribute loss"
                self.encounter_condition_tree.insert("", "end", iid=condition.id, text=condition.name, values=(condition.source, state))
            character = item.character()
            if character:
                for gear in character.equipment:
                    self.encounter_gear_tree.insert("", "end", iid=gear.id, text=gear.name, values=(gear.category, f"{gear.adjusted_price:g}", f"{gear.capacity:g}"))
            for resource in item.resources:
                self.encounter_resource_tree.insert("", "end", iid=resource.id, text=resource.name, values=(resource.current, resource.maximum if resource.maximum is not None else "—"))
            if not notes_has_focus:
                self.encounter_notes_text.insert("1.0", item.notes)
        self.encounter_loot_tree.delete(*self.encounter_loot_tree.get_children())
        self.encounter_objective_tree.delete(*self.encounter_objective_tree.get_children())
        self.encounter_environment_tree.delete(*self.encounter_environment_tree.get_children())
        self.encounter_log_tree.delete(*self.encounter_log_tree.get_children())
        if session:
            for loot in session.loot:
                state = "Excluded" if loot.excluded else "Revealed" if loot.revealed else "Staged"
                value = f"{loot.credits} cr" if loot.credits else f"{loot.value:g}"
                self.encounter_loot_tree.insert("", "end", iid=loot.id, text=loot.name, values=(loot.source_name, loot.quantity, value, state))
            for objective in session.objectives:
                self.encounter_objective_tree.insert("", "end", iid=objective.id, text=objective.name, values=(f"{objective.progress}/{objective.target}", objective.state, objective.timer if objective.timer is not None else "—"))
            for modifier in session.environment:
                self.encounter_environment_tree.insert("", "end", iid=modifier.id, text=modifier.name, values=(modifier.zone, modifier.target, f"{modifier.amount:+d}", "Active" if modifier.active else "Off"))
            for event in reversed(self.encounter_store.list_events(session.id)[-100:]):
                self.encounter_log_tree.insert("", "end", iid=event.id, text=event.summary, values=(event.round, event.event_type))
        for name, state in tree_states.items():
            self._restore_tree_state(getattr(self, name), state)
        self._refresh_encounter_quadrants(item)
        self._refresh_encounter_action_picker(item)
        if item and not self.encounter_action_tree.selection() and self.encounter_action_tree.get_children():
            self.encounter_action_tree.selection_set(self.encounter_action_tree.get_children()[0])
        self._refresh_encounter_selected_ammo()
        self._refresh_encounter_compact_inspector(item, selected_entity)
        self._refresh_encounter_targets(item)

    def _ensure_encounter(self, name: str = "Ad Hoc Encounter") -> bool:
        if self.current_encounter:
            return True
        session = EncounterSession(name=name, campaign_id=self._selected_campaign_id())
        self.encounter_store.save_session(session)
        self._set_current_encounter(session)
        return True

    def _encounter_add_character(self, character: Character, *, faction: str = "Enemies", minion: bool = False) -> None:
        self._ensure_encounter()
        combatant = Combatant.from_character(character, faction=faction, minion=minion)
        combatant.actions.extend(self.encounter_store.list_actions(character.id))

        def operation(session: EncounterSession) -> None:
            session.combatants.append(combatant)
            stage_loot(session)

        self._encounter_mutate("Roster", f"Added {combatant.name}", operation, actor_id=combatant.id)

    def _encounter_add_current(self) -> None:
        if not self.current_character:
            messagebox.showwarning("No character", "Generate or load a character first.")
            return
        self._encounter_add_character(self.current_character)

    def _encounter_add_saved(self) -> None:
        characters = self.character_store.list()
        if not characters:
            messagebox.showwarning("No saved characters", "Save a character first.")
            return
        window = tk.Toplevel(self.root)
        window.title("Add saved combatants")
        window.geometry("560x430")
        tree = ttk.Treeview(window, columns=("Race", "Role", "PL"), show="tree headings", selectmode="extended")
        tree.heading("#0", text="Name")
        for column in ("Race", "Role", "PL"):
            tree.heading(column, text=column)
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        by_id = {item.id: item for item in characters}
        for item in characters:
            tree.insert("", "end", iid=item.id, text=item.name or "—", values=(item.race, item.role, item.power_level))
        options = ttk.Frame(window, padding=8)
        options.pack(fill="x")
        faction = tk.StringVar(value="Enemies")
        minion = tk.BooleanVar(value=False)
        ttk.Label(options, text="Faction:").pack(side="left")
        ttk.Entry(options, textvariable=faction, width=15).pack(side="left", padx=3)
        ttk.Checkbutton(options, text="Minion", variable=minion).pack(side="left")

        def add() -> None:
            for item_id in tree.selection():
                self._encounter_add_character(by_id[item_id], faction=faction.get().strip() or "Enemies", minion=minion.get())
            window.destroy()

        ttk.Button(options, text="Add selected", command=add).pack(side="right")

    def _encounter_add_ad_hoc(self) -> None:
        options = sorted({item.faction for item in self.current_encounter.combatants} | {"Enemies", "Allies", "Neutral"}) if self.current_encounter else ["Enemies", "Allies", "Neutral"]
        values = prompt_form(self.root, "Add ad-hoc combatant", (
            FormField("name", "Name"),
            FormField("pl", "Power Level", "int", 5, minimum=1, maximum=30),
            FormField("faction", "Faction", "choice", "Enemies", tuple(options)),
            FormField("minion", "Use minion rules", "bool", False),
        ), submit_label="Add combatant")
        if not values or not values["name"]:
            return
        combatant = Combatant.ad_hoc(values["name"], values["pl"], faction=values["faction"], minion=values["minion"])
        self._ensure_encounter()
        self._encounter_mutate("Roster", f"Added {combatant.name}", lambda session: session.combatants.append(combatant), actor_id=combatant.id)

    def _encounter_add_marker(self, marker: Objective | None = None) -> None:
        """Add or edit a statless initiative turn marker."""
        self._ensure_encounter()
        window = tk.Toplevel(self.root)
        window.title("Edit initiative label" if marker else "Add initiative label")
        window.transient(self.root)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=10)
        body.pack(fill="both", expand=True)
        name_var = tk.StringVar(value=marker.name if marker else "Players")
        initiative_var = tk.StringVar(value=str(marker.initiative if marker and marker.initiative is not None else 10))
        ttk.Label(body, text="Label").grid(row=0, column=0, sticky="w")
        name_entry = ttk.Entry(body, textvariable=name_var, width=34)
        name_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(body, text="Initiative").grid(row=1, column=0, sticky="w", pady=(6, 0))
        initiative_entry = ttk.Spinbox(body, textvariable=initiative_var, from_=-100, to=200, width=8)
        initiative_entry.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            initiative_entry.bind(sequence, lambda event: self._adjust_prompt_number(initiative_var, event, minimum=-100, maximum=200, default=10))
        ttk.Label(body, text="Recent labels — select one or more, then add" if not marker else "Recent labels — double-click to use").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 3))
        recent = ttk.Treeview(body, columns=("Initiative",), show="tree headings", height=5, selectmode="extended" if not marker else "browse")
        recent.heading("#0", text="Label")
        recent.heading("Initiative", text="Init")
        recent.column("#0", width=210)
        recent.column("Initiative", width=60, anchor="center")
        recent.grid(row=3, column=0, columnspan=2, sticky="ew")
        for index, (saved_name, saved_initiative) in enumerate(self.initiative_presets):
            recent.insert("", "end", iid=str(index), text=saved_name, values=(saved_initiative,))

        def submit(preset: tuple[str, int] | None = None) -> None:
            label = (preset[0] if preset else name_var.get()).strip()
            try:
                initiative = preset[1] if preset else int(initiative_var.get())
            except ValueError:
                messagebox.showwarning("Invalid initiative", "Initiative must be a whole number.", parent=window)
                return
            if not label:
                messagebox.showwarning("Missing label", "Enter a label for this turn.", parent=window)
                return
            initiative = max(-100, min(200, initiative))
            identity = marker.id if marker else str(uuid4())
            if marker:
                def operation(session: EncounterSession) -> None:
                    target = next(item for item in session.objectives if item.id == marker.id)
                    target.name = label
                    target.initiative = initiative
                summary = f"Updated turn marker {label}"
            else:
                def operation(session: EncounterSession) -> None:
                    session.objectives.append(Objective(id=identity, name=label, kind="Marker", initiative=initiative, target=1))
                summary = f"Added turn marker {label}"
            pair = (label, initiative)
            self.initiative_presets = [pair] + [entry for entry in self.initiative_presets if entry != pair]
            self.initiative_presets = self.initiative_presets[:8]
            try:
                self._save_user_settings()
            except OSError:
                pass
            self._encounter_mutate("Initiative", summary, operation, actor_id=identity)
            if self.encounter_tree.exists(identity):
                self.encounter_tree.selection_set(identity)
                self._refresh_encounter_inspector()
            window.destroy()

        def use_recent(_event=None) -> None:
            selected = recent.selection()
            if selected:
                presets = [self.initiative_presets[int(index)] for index in selected]
                if marker:
                    submit(presets[0])
                else:
                    self._encounter_add_recent_markers(presets)
                    window.destroy()

        recent.bind("<Double-1>", use_recent)
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="right")
        if not marker:
            ttk.Button(buttons, text="Add selected", command=use_recent).pack(side="right", padx=(0, 5))
        ttk.Button(buttons, text="Save" if marker else "Add", command=submit, style="EncounterPrimary.TButton").pack(side="right", padx=(0, 5))
        name_entry.bind("<Return>", lambda _event: submit())
        initiative_entry.bind("<Return>", lambda _event: submit())
        name_entry.focus_set()

    def _encounter_add_recent_markers(self, presets: Iterable[tuple[str, int]]) -> None:
        """Add selected initiative presets as a single undoable encounter update."""
        selected = list(presets)
        if not selected:
            return
        self._ensure_encounter()
        identities = [str(uuid4()) for _preset in selected]

        def operation(session: EncounterSession) -> None:
            session.objectives.extend(
                Objective(id=identity, name=label, kind="Marker", initiative=initiative, target=1)
                for identity, (label, initiative) in zip(identities, selected)
            )

        self._encounter_mutate(
            "Initiative",
            f"Added {len(selected)} turn marker(s): {', '.join(label for label, _initiative in selected)}",
            operation,
            actor_id=identities[0],
        )

    def _encounter_roster_context_menu(self, event) -> str:
        row_id = self.encounter_tree.identify_row(event.y)
        if not row_id:
            return "break"
        self.encounter_tree.selection_set(row_id)
        marker = self._selected_encounter_objective()
        if marker:
            entries = [("Edit turn label…", lambda: self._encounter_add_marker(marker)), ("Remove", self._encounter_remove_selected)]
        else:
            item = self._selected_encounter_combatant()
            if not item:
                return "break"
            entries = self._encounter_character_menu_entries(item, include_roster_actions=True)
        return self._popup_encounter_menu(event, entries)

    def _encounter_remove_selected(self) -> None:
        item = self._selected_encounter_combatant()
        if item and messagebox.askyesno("Remove combatant?", f"Remove {item.name} from this session?"):
            self._encounter_mutate("Roster", f"Removed {item.name}", lambda session: setattr(session, "combatants", [actor for actor in session.combatants if actor.id != item.id]), actor_id=item.id)
            return
        marker = self._selected_encounter_objective()
        if marker and messagebox.askyesno("Remove initiative label?", f"Remove {marker.name} from this session?"):
            self._encounter_mutate("Roster", f"Removed {marker.name}", lambda session: setattr(session, "objectives", [entry for entry in session.objectives if entry.id != marker.id]), actor_id=marker.id)

    def _encounter_generator_defaults(self) -> GeneratorSettings | None:
        """Use the Character Creator profile while always rolling race and role."""
        settings = self._character_settings()
        if settings is None:
            return None
        settings.race = "Random"
        settings.role = "Random"
        settings.guarantee_weapon = bool(self.encounter_guarantee_weapon_var.get())
        return settings

    def _encounter_quick_generate(self) -> None:
        self._ensure_encounter("Generated Encounter")
        campaign = self.encounter_store.get_campaign(self.current_encounter.campaign_id) if self.current_encounter and self.current_encounter.campaign_id else None
        party = [character for character in self.character_store.list() if campaign and character.id in campaign.party_character_ids]
        saved_size = self.saved_encounter_generation_settings.get("party_size")
        saved_pl = self.saved_encounter_generation_settings.get("party_power_level")
        default_size = saved_size if isinstance(saved_size, int) and 1 <= saved_size <= 20 else len(party) or 4
        default_pl = saved_pl if isinstance(saved_pl, int) and 1 <= saved_pl <= 30 else round(sum(character.power_level for character in party) / len(party)) if party else 10
        values = prompt_form(self.root, "Quick encounter generation", (
            FormField("party_size", "Party size", "int", default_size, minimum=1, maximum=20),
            FormField("party_pl", "Average party PL", "int", default_pl, minimum=1, maximum=30),
            FormField("difficulty", "Difficulty", "choice", "Standard", tuple(DIFFICULTY_MULTIPLIERS)),
            FormField("seed", "Seed (blank for random)", "optional_int"),
        ), submit_label="Preview encounter")
        if values is None:
            return
        if not self.snapshot:
            messagebox.showwarning("Catalog unavailable", "Wait for the catalog to load before generating opponents.")
            return
        defaults = self._encounter_generator_defaults()
        if defaults is None:
            return
        self.saved_encounter_generation_settings = {"party_size": values["party_size"], "party_power_level": values["party_pl"]}
        try:
            self._save_user_settings()
        except OSError:
            LOGGER.exception("Could not save encounter generation settings")
        roster = self.encounter_builder.quick_generate(
            self.snapshot, party_size=values["party_size"], party_power_level=values["party_pl"],
            difficulty=values["difficulty"], seed=values["seed"], base_settings=defaults)
        score = sum(threat_score(item.power_level, values["party_pl"], minion=item.minion) for item in roster)
        target = target_threat(values["party_size"], values["difficulty"])
        if not messagebox.askyesno("Generated roster",
            f"Generated {len(roster)} opponents.\nThreat {score:.2f} / target {target:.2f}.\n\nAdd this roster?", parent=self.root):
            return
        def operation(session: EncounterSession) -> None:
            session.combatants.extend(roster)
            stage_loot(session)
        self._encounter_mutate("Generation", f"Quick-generated {len(roster)} combatants ({score:.2f}/{target:.2f})", operation)

    def _encounter_advanced_editor(self) -> None:
        template = self.encounter_store.get_template(self._selected_template_id())
        if not template:
            template = EncounterTemplate(campaign_id=self._selected_campaign_id(), name="Advanced Encounter")
        window = tk.Toplevel(self.root)
        window.title("Advanced encounter recipe")
        window.geometry("820x500")
        tree = ttk.Treeview(window, columns=("Source", "Qty", "Faction", "Minion", "PL/Role"), show="tree headings")
        tree.heading("#0", text="Slot")
        for column in ("Source", "Qty", "Faction", "Minion", "PL/Role"):
            tree.heading(column, text=column)
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Label(window, text="Generated slots inherit the Character Creator race chances unless their GeneratorSettings JSON overrides them.").pack(fill="x", padx=8)

        def refresh() -> None:
            tree.delete(*tree.get_children())
            for slot in template.recipe_slots:
                tree.insert("", "end", iid=slot.id, text=slot.name, values=(slot.source_type, f"{slot.quantity_min}-{slot.quantity_max}", slot.faction, "Yes" if slot.minion else "", f"PL {slot.settings.get('power_level', template.party_power_level)} / {slot.settings.get('role', 'Random')}"))

        def add_generated() -> None:
            values = prompt_form(window, "Add generated opponent", (
                FormField("name", "Name", value="Generated enemy"),
                FormField("quantity", "Quantity", "int", 1, minimum=1, maximum=100),
                FormField("pl", "Power Level", "int", template.party_power_level, minimum=1, maximum=30),
                FormField("role", "Role", "choice", "Random", ("Random", *ROLE_WEIGHTS)),
            ), submit_label="Add slot")
            if not values or not values["name"]:
                return
            template.recipe_slots.append(RecipeSlot(name=values["name"], quantity_min=values["quantity"],
                quantity_max=values["quantity"], settings={"power_level": values["pl"], "role": values["role"], "race": "Random"}))
            refresh()

        def add_saved() -> None:
            characters = self.character_store.list()
            if not characters:
                messagebox.showwarning("No saved characters", "Save a character first.", parent=window)
                return
            chosen = choose_records(window, "Choose saved combatants", characters,
                (("Name", lambda item: item.name, 240), ("Race", lambda item: item.race, 175),
                 ("PL", lambda item: item.power_level, 60)), multiple=True, submit_label="Add combatants")
            for character in chosen:
                template.recipe_slots.append(RecipeSlot(source_type="Saved", source_character_id=character.id, name=character.name, settings={"power_level": character.power_level}))
            refresh()

        def add_ad_hoc() -> None:
            values = prompt_form(window, "Add ad-hoc opponent", (
                FormField("name", "Name", value="Ad-hoc enemy"),
                FormField("pl", "Power Level", "int", template.party_power_level, minimum=1, maximum=30),
            ), submit_label="Add slot")
            if values and values["name"]:
                template.recipe_slots.append(RecipeSlot(source_type="Ad Hoc", name=values["name"], settings={"power_level": values["pl"]}))
                refresh()

        def edit_slot() -> None:
            selected = tree.selection()
            slot = next((item for item in template.recipe_slots if selected and item.id == selected[0]), None)
            if not slot:
                return
            slot.name = simpledialog.askstring("Recipe slot", "Name:", initialvalue=slot.name, parent=window) or slot.name
            slot.quantity_min = simpledialog.askinteger("Recipe slot", "Minimum quantity:", initialvalue=slot.quantity_min, minvalue=0, maxvalue=100, parent=window) or 0
            maximum = simpledialog.askinteger("Recipe slot", "Maximum quantity:", initialvalue=slot.quantity_max, minvalue=slot.quantity_min, maxvalue=100, parent=window)
            slot.quantity_max = slot.quantity_min if maximum is None else maximum
            slot.faction = simpledialog.askstring("Recipe slot", "Faction:", initialvalue=slot.faction, parent=window) or slot.faction
            slot.initiative_group = simpledialog.askstring("Recipe slot", "Initiative group (blank for individual):", initialvalue=slot.initiative_group, parent=window) or ""
            slot.minion = messagebox.askyesno("Recipe slot", "Use minion rules?", parent=window)
            slot.required = messagebox.askyesno("Recipe slot", "Required slot?", parent=window)
            slot.unique = messagebox.askyesno("Recipe slot", "Unique source?", parent=window)
            slot.locked = messagebox.askyesno("Recipe slot", "Lock generated result against rerolls?", parent=window)
            weight = simpledialog.askfloat("Recipe slot", "Generation weight:", initialvalue=slot.weight, minvalue=0, parent=window)
            if weight is not None:
                slot.weight = weight
            override = simpledialog.askstring("Recipe slot", "Threat override (blank for formula):", initialvalue="" if slot.threat_override is None else str(slot.threat_override), parent=window)
            try:
                slot.threat_override = None if not (override or "").strip() else max(0.0, float(override))
            except ValueError:
                messagebox.showwarning("Invalid threat", "Threat override was left unchanged.", parent=window)
            if slot.source_type == "Generated":
                settings_json = simpledialog.askstring("Recipe slot", "GeneratorSettings JSON (all fields are supported):", initialvalue=json.dumps(slot.settings, ensure_ascii=False), parent=window)
                if settings_json:
                    try:
                        parsed = json.loads(settings_json)
                        if not isinstance(parsed, dict):
                            raise ValueError("settings must be a JSON object")
                        unknown = set(parsed) - set(GeneratorSettings.__dataclass_fields__)
                        if unknown:
                            raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")
                        slot.settings = parsed
                    except (ValueError, json.JSONDecodeError) as exc:
                        messagebox.showwarning("Invalid settings", str(exc), parent=window)
            refresh()

        def remove() -> None:
            selected = set(tree.selection())
            template.recipe_slots = [slot for slot in template.recipe_slots if slot.id not in selected]
            refresh()

        def save() -> None:
            name = simpledialog.askstring("Recipe", "Template name:", initialvalue=template.name, parent=window)
            if name:
                template.name = name.strip()
                template.updated_at = datetime.now().astimezone().isoformat()
                self.encounter_store.save_template(template)
                self._refresh_encounter_library()
                window.destroy()

        def build() -> None:
            if not self.snapshot:
                messagebox.showwarning("Catalog unavailable", "Wait for the live catalog before building generated slots.", parent=window)
                return
            seed_text = simpledialog.askstring("Build recipe", "Deterministic seed (blank for random):", initialvalue="", parent=window) or ""
            try:
                seed = int(seed_text) if seed_text.strip() else None
            except ValueError:
                messagebox.showwarning("Invalid seed", "The seed must be a whole number.", parent=window)
                return
            defaults = self._encounter_generator_defaults()
            if defaults is None:
                return
            roster = self.encounter_builder.build_recipe(template, self.snapshot, {item.id: item for item in self.character_store.list()}, seed=seed, base_settings=defaults)
            session = EncounterSession(template_id=template.id, campaign_id=template.campaign_id, name=template.name, combatants=roster, objectives=copy.deepcopy(template.objectives), environment=copy.deepcopy(template.environment), notes=template.notes)
            stage_loot(session)
            self.encounter_store.save_session(session)
            self._set_current_encounter(session)
            self._refresh_encounter_library()
            window.destroy()

        controls = ttk.Frame(window, padding=8)
        controls.pack(fill="x")
        ttk.Button(controls, text="Add generated slot", command=add_generated).pack(side="left")
        ttk.Button(controls, text="Add saved", command=add_saved).pack(side="left", padx=3)
        ttk.Button(controls, text="Add ad hoc", command=add_ad_hoc).pack(side="left")
        ttk.Button(controls, text="Edit", command=edit_slot).pack(side="left", padx=3)
        ttk.Button(controls, text="Remove", command=remove).pack(side="left", padx=3)
        ttk.Button(controls, text="Save recipe", command=save).pack(side="right")
        ttk.Button(controls, text="Build encounter", command=build).pack(side="right", padx=3)
        refresh()

    def _encounter_roll_initiative(self) -> None:
        if self.encounter_controller:
            self._clear_encounter_resolution_preview()
            self.encounter_controller.roll_initiative()
            self.current_encounter = self.encounter_controller.session
            self._refresh_encounter_view()

    def _encounter_set_initiative(self) -> None:
        item: Combatant | Objective | None = self._selected_encounter_combatant() or self._selected_encounter_objective()
        if not item:
            return
        value = simpledialog.askinteger("Initiative", f"Initiative for {item.name}:", initialvalue=item.initiative or 0, minvalue=-100, maxvalue=200, parent=self.root)
        if value is not None:
            def operation(session: EncounterSession) -> None:
                entities: list[Combatant | Objective] = [*session.combatants, *session.objectives]
                setattr(next(entity for entity in entities if entity.id == item.id), "initiative", value)
            self._encounter_mutate("Initiative", f"Set {item.name} initiative to {value}", operation, actor_id=item.id)

    def _encounter_next_turn(self) -> None:
        if getattr(self, "_condition_roll_prompt_active", False):
            return
        if self.encounter_controller:
            ordered = self.current_encounter.ordered_turn_entities() if self.current_encounter else []
            upcoming = ordered[(self.current_encounter.turn_index + 1) % len(ordered)] if ordered else None
            rolls = None
            if isinstance(upcoming, Combatant):
                conditions = [entry for entry in upcoming.conditions if entry.active and not entry.suppressed and entry.name in CUSTOM_CONDITIONS]
                rolls = self._prompt_condition_rolls(upcoming, conditions, "Start-of-turn condition rolls")
                if rolls is None:
                    return
            self._clear_encounter_resolution_preview()
            self.encounter_controller.next_turn(rolls)
            self.current_encounter = self.encounter_controller.session
            self._refresh_encounter_view()

    def _toggle_encounter_flag(self, field: str) -> None:
        item = self._selected_encounter_combatant()
        if item:
            self._encounter_mutate("Turn", f"Toggled {field} for {item.name}", lambda session: setattr(next(actor for actor in session.combatants if actor.id == item.id), field, not getattr(next(actor for actor in session.combatants if actor.id == item.id), field)), actor_id=item.id)

    def _encounter_toggle_defeated(self) -> None:
        item = self._selected_encounter_combatant()
        if item and self.encounter_controller:
            self._clear_encounter_resolution_preview()
            self.encounter_controller.mark_defeated(item.id, not item.defeated)
            self.current_encounter = self.encounter_controller.session
            self._refresh_encounter_view()

    def _encounter_bruise(self, amount: int) -> None:
        item = self._selected_encounter_combatant()
        if item and self.encounter_controller:
            self._clear_encounter_resolution_preview()
            self.encounter_controller.add_bruise(item.id, amount)
            self.current_encounter = self.encounter_controller.session
            self._refresh_encounter_view()

    def _update_condition_fields(self) -> None:
        duration = self.encounter_condition_duration_var.get()
        self.encounter_condition_rounds_entry.configure(state="normal" if duration == "fixed_rounds" else "disabled")
        save_state = "readonly" if duration == "save_ends" else "disabled"
        self.encounter_condition_recovery_box.configure(state=save_state)
        self.encounter_condition_dc_entry.configure(state="normal" if duration == "save_ends" else "disabled")

    def _encounter_add_condition_dialog(self) -> None:
        if not self._selected_encounter_combatant():
            return
        window = tk.Toplevel(self.root)
        window.title("Add encounter condition")
        window.transient(self.root)
        window.resizable(False, False)
        values = {
            "name": tk.StringVar(value=self.encounter_condition_name_var.get()),
            "duration": tk.StringVar(value=self.encounter_condition_duration_var.get()),
            "rounds": tk.StringVar(value=self.encounter_condition_rounds_var.get()),
            "recovery": tk.StringVar(value=self.encounter_condition_recovery_var.get()),
            "dc": tk.StringVar(value=self.encounter_condition_dc_var.get()),
        }
        form = ttk.Frame(window, padding=10)
        form.pack(fill="both", expand=True)
        fields = (
            ("Condition", "name", STANDARD_CONDITIONS),
            ("Duration", "duration", ("manual", "start_turn", "end_turn", "fixed_rounds", "save_ends", "scene", "permanent")),
            ("Rounds", "rounds", ()), ("Recovery defense", "recovery", DEFENSE_KEYS), ("Recovery DC", "dc", ()),
        )
        form.columnconfigure(1, weight=1)
        for row, (label, key, options) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
            widget = ttk.Combobox(form, textvariable=values[key], values=options, state="readonly" if key in {"duration", "recovery"} else "normal") if options else ttk.Entry(form, textvariable=values[key])
            widget.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)

        def add() -> None:
            self.encounter_condition_name_var.set(values["name"].get())
            self.encounter_condition_duration_var.set(values["duration"].get())
            self.encounter_condition_rounds_var.set(values["rounds"].get())
            self.encounter_condition_recovery_var.set(values["recovery"].get())
            self.encounter_condition_dc_var.set(values["dc"].get())
            self._encounter_add_condition()
            window.destroy()

        row = ttk.Frame(form)
        row.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(row, text="Cancel", command=window.destroy).pack(side="left")
        ttk.Button(row, text="Add", command=add, style="EncounterPrimary.TButton").pack(side="left", padx=(4, 0))

    def _encounter_edit_notes(self) -> None:
        item = self._selected_encounter_combatant()
        if not item:
            return
        window = tk.Toplevel(self.root)
        window.title(f"Notes — {item.name}")
        window.transient(self.root)
        editor = tk.Text(window, wrap="word", width=60, height=12)
        editor.pack(fill="both", expand=True, padx=8, pady=8)
        editor.insert("1.0", item.notes)

        def save() -> None:
            notes = editor.get("1.0", "end-1c")
            self._encounter_mutate("Notes", f"Updated notes for {item.name}", lambda session: setattr(next(actor for actor in session.combatants if actor.id == item.id), "notes", notes), actor_id=item.id)
            window.destroy()

        ttk.Button(window, text="Save Notes", command=save, style="EncounterPrimary.TButton").pack(anchor="e", padx=8, pady=(0, 8))
        editor.focus_set()

    def _encounter_toggle_modifier(self, modifier_id: str) -> None:
        item = self._selected_encounter_combatant()
        if item:
            self._encounter_mutate("Modifier", f"Toggled modifier on {item.name}", lambda session: setattr(next(modifier for modifier in next(actor for actor in session.combatants if actor.id == item.id).modifiers if modifier.id == modifier_id), "active", not next(modifier for modifier in next(actor for actor in session.combatants if actor.id == item.id).modifiers if modifier.id == modifier_id).active), actor_id=item.id)

    def _print_encounter_gear(self, gear_id: str) -> None:
        item = self._selected_encounter_combatant()
        character = item.character() if item else None
        gear = next((entry for entry in character.equipment if entry.id == gear_id), None) if character else None
        if gear:
            self.spooler.enqueue([PrintDocument(gear.name, gear.category, essential_item_text(gear))], 1)

    def _encounter_add_condition(self) -> None:
        item = self._selected_encounter_combatant()
        if not item:
            return
        name = self.encounter_condition_name_var.get().strip()
        if not name:
            self.encounter_status_var.set("Enter a standard or custom condition name.")
            return
        duration = self.encounter_condition_duration_var.get()
        custom_name = name.title()
        original_rank = 0
        if custom_name in {"Burning", "Poisoned"}:
            requested_rank = simpledialog.askinteger(
                f"{custom_name} source", "Original attack rank:", initialvalue=item.power_level,
                minvalue=0, maxvalue=999, parent=self.root,
            )
            if requested_rank is None:
                return
            original_rank = requested_rank
            duration = "custom"
            rounds = random.randint(1, 6)
        else:
            rounds = None
        try:
            if duration == "fixed_rounds":
                rounds = max(1, int(self.encounter_condition_rounds_var.get()))
            dc = max(0, int(self.encounter_condition_dc_var.get())) if duration == "save_ends" else 0
        except ValueError:
            self.encounter_status_var.set("Condition rounds and recovery DC must be whole numbers.")
            return
        recovery = self.encounter_condition_recovery_var.get().upper() if duration == "save_ends" else ""
        if custom_name in CUSTOM_CONDITIONS:
            duration = "custom"
            dc = 10 if custom_name in {"Bleeding", "Poisoned", "Dying"} else 0
        condition = ConditionInstance(
            name=custom_name, start_round=self.current_encounter.round if self.current_encounter else 1,
            duration_mode=duration, rounds_remaining=rounds, recovery_defense=recovery, dc=dc,
            original_rank=original_rank,
        )
        if self.encounter_controller:
            self._clear_encounter_resolution_preview()
            self.encounter_controller.add_condition(item.id, condition)
            self.current_encounter = self.encounter_controller.session
            self._refresh_encounter_view()

    def _encounter_remove_condition(self) -> None:
        item = self._selected_encounter_combatant()
        selected = self.encounter_condition_tree.selection()
        if item and selected:
            condition_id = selected[0]
            def operation(session: EncounterSession) -> None:
                actor = next(actor for actor in session.combatants if actor.id == item.id)
                condition = next(condition for condition in actor.conditions if condition.id == condition_id)
                RulesEngine.deactivate_condition(actor, condition)
                actor.conditions.remove(condition)
            self._encounter_mutate("Condition", "Removed condition", operation, actor_id=item.id)

    def _prompt_condition_rolls(self, target: Combatant, conditions: list[ConditionInstance], title: str) -> dict[str, dict[str, int]] | None:
        if getattr(self, "_condition_roll_prompt_active", False):
            return None
        requirements = [(condition, key) for condition in conditions for key in RulesEngine.condition_roll_requirements(condition)]
        if not requirements:
            return {}
        self._condition_roll_prompt_active = True
        try:
            window = tk.Toplevel(self.root)
            window.title(title)
            window.transient(self.root)
            window.resizable(False, False)
            window.grab_set()
            form = ttk.Frame(window, padding=12)
            form.pack(fill="both", expand=True)
            ttk.Label(form, text=target.name, font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
            variables: dict[tuple[str, str], tk.StringVar] = {}
            first_entry = None
            for row, (condition, key) in enumerate(requirements, 1):
                label = "Toughness damage" if key == "damage" else "Fortitude progression" if key == "progression" else "Stamina save"
                dc = f" · DC {10 + condition.saves_made}" if key in {"progression", "save"} else ""
                ttk.Label(form, text=f"{condition.name}: {label}{dc}").grid(row=row, column=0, sticky="w", pady=2)
                variable = tk.StringVar()
                variables[(condition.id, key)] = variable
                entry = ttk.Entry(form, textvariable=variable, width=5)
                entry.grid(row=row, column=1, padx=6)
                entry.bind("<space>", lambda _event: "break")
                first_entry = first_entry or entry
                ttk.Button(form, text="Roll", command=lambda value=variable: value.set(str(random.randint(1, 20)))).grid(row=row, column=2)
            result: dict[str, dict[str, int]] | None = None

            def accept() -> None:
                nonlocal result
                try:
                    parsed: dict[str, dict[str, int]] = {}
                    for (condition_id, key), variable in variables.items():
                        die = int(variable.get())
                        if not 1 <= die <= 20:
                            raise ValueError
                        parsed.setdefault(condition_id, {})[key] = die
                except ValueError:
                    messagebox.showwarning("Invalid roll", "Every d20 result must be between 1 and 20.", parent=window)
                    return
                result = parsed
                window.destroy()

            controls = ttk.Frame(form)
            controls.grid(row=len(requirements) + 1, column=0, columnspan=3, sticky="e", pady=(8, 0))
            ttk.Button(controls, text="Cancel", command=window.destroy).pack(side="left")
            ttk.Button(controls, text="Apply", command=accept, style="EncounterPrimary.TButton").pack(side="left", padx=(5, 0))
            window.bind("<Return>", lambda _event: accept())
            window.bind("<Escape>", lambda _event: window.destroy())
            if first_entry:
                first_entry.focus_set()
            window.wait_window()
            return result
        finally:
            self._condition_roll_prompt_active = False

    def _encounter_force_condition_tick(self) -> None:
        item = self._selected_encounter_combatant()
        selected = self.encounter_condition_tree.selection()
        condition = next((entry for entry in item.conditions if selected and entry.id == selected[0]), None) if item else None
        if not item or not condition or not condition.active or condition.name not in CUSTOM_CONDITIONS or not self.encounter_controller:
            self.encounter_status_var.set("Select an active Burning, Bleeding, Poisoned, Diseased, or Dying condition.")
            return
        rolls = self._prompt_condition_rolls(item, [condition], f"Force {condition.name} tick")
        if rolls is None:
            return
        self.encounter_controller.force_condition_tick(item.id, condition.id, rolls.get(condition.id, {}))
        self.current_encounter = self.encounter_controller.session
        self._refresh_encounter_view()

    def _encounter_recover_condition(self) -> None:
        item = self._selected_encounter_combatant()
        selected = self.encounter_condition_tree.selection()
        condition = next((entry for entry in item.conditions if selected and entry.id == selected[0]), None) if item else None
        if not item or not condition:
            return
        die = simpledialog.askinteger("Recovery check", "d20 result (Cancel for app roll):", minvalue=1, maxvalue=20, parent=self.root)
        die = random.randint(1, 20) if die is None else die
        _abilities, defenses, _speed = RulesEngine.stats(item, self.current_encounter.environment if self.current_encounter else ())
        total = die + defenses.get(condition.recovery_defense, 0)
        success = total >= condition.recovery_dc
        if messagebox.askyesno("Recovery result", f"{die} + {defenses.get(condition.recovery_defense, 0)} = {total} vs DC {condition.recovery_dc}.\n\n{'Recover' if success else 'Keep condition active'}?", parent=self.root):
            self._encounter_mutate("Recovery", f"Recovery check for {condition.name}: {total} vs {condition.recovery_dc}", lambda session: setattr(next(entry for entry in next(actor for actor in session.combatants if actor.id == item.id).conditions if entry.id == condition.id), "active", not success), actor_id=item.id, dice={"die": die, "total": total, "dc": condition.recovery_dc})

    def _encounter_new_action(self) -> None:
        item = self._selected_encounter_combatant()
        if not item:
            return
        window = tk.Toplevel(self.root)
        window.title(f"New action — {item.name}")
        window.transient(self.root)
        window.resizable(False, False)
        values = {
            "name": tk.StringVar(), "effect": tk.StringVar(value="Damage"), "rank": tk.StringVar(value=str(item.power_level)),
            "range": tk.StringVar(value="Close"), "attack": tk.StringVar(value="0"), "target": tk.StringVar(value="PARRY"),
            "resistance": tk.StringVar(value="TOUGH"), "dc": tk.StringVar(),
            "degrees": tk.StringVar(value="Dazed / Stunned / Incapacitated"),
            "persistent": tk.BooleanVar(value=False),
        }
        form = ttk.Frame(window, padding=10)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        fields = (
            ("Name", "name", "entry", ()), ("Effect", "effect", "combo", ("Damage", "Affliction", "Check", "Healing", "Custom")),
            ("Rank", "rank", "entry", ()), ("Range", "range", "combo", ("Close", "Ranged")),
            ("Attack modifier", "attack", "entry", ()), ("Target defense", "target", "combo", DEFENSE_KEYS),
            ("Resistance", "resistance", "combo", DEFENSE_KEYS), ("DC override (blank = derived)", "dc", "entry", ()),
            ("Affliction degrees (/ separated)", "degrees", "entry", ()),
        )
        for row_index, (label, key, kind, options) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row_index, column=0, sticky="w", pady=2)
            if kind == "combo":
                widget = ttk.Combobox(form, textvariable=values[key], values=options, state="readonly")
            else:
                widget = ttk.Entry(form, textvariable=values[key])
            widget.grid(row=row_index, column=1, sticky="ew", padx=(8, 0), pady=2)
        ttk.Checkbutton(form, text="Persistent healing", variable=values["persistent"]).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=2)

        def save() -> None:
            try:
                name = values["name"].get().strip()
                if not name:
                    raise ValueError("Action name is required.")
                rank = int(values["rank"].get())
                attack_modifier = int(values["attack"].get())
                dc_text = values["dc"].get().strip()
                dc = int(dc_text) if dc_text else None
                if min(rank, dc if dc is not None else 0) < 0:
                    raise ValueError("Rank and DC cannot be negative.")
            except ValueError as exc:
                messagebox.showwarning("Invalid action", str(exc), parent=window)
                return
            action_range = values["range"].get()
            action = ActionCard(
                name=name, effect_type=values["effect"].get(), rank=rank, range=action_range,
                attack_basis="DEX" if action_range == "Ranged" else "FGT", attack_modifier=attack_modifier,
                target_defense=values["target"].get(), resistance_defense=values["resistance"].get(), dc=dc,
                persistent=bool(values["persistent"].get()) and values["effect"].get().casefold() == "healing",
            )
            if action.effect_type.casefold() == "affliction":
                action.degree_conditions = {str(index): [value.strip()] for index, value in enumerate(values["degrees"].get().split("/"), 1) if value.strip()}
            self._encounter_mutate("Action", f"Added action {action.name}", lambda session: next(actor for actor in session.combatants if actor.id == item.id).actions.append(action), actor_id=item.id)
            window.destroy()

        controls = ttk.Frame(form)
        controls.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(controls, text="Cancel", command=window.destroy).pack(side="left")
        ttk.Button(controls, text="Add Action", command=save, style="EncounterPrimary.TButton").pack(side="left", padx=(5, 0))
        window.bind("<Return>", lambda _event: save())

    def _encounter_delete_action(self) -> None:
        item = self._selected_encounter_combatant()
        selected = self.encounter_action_tree.selection()
        if item and selected:
            action_id = selected[0]
            self._encounter_mutate("Action", "Deleted encounter action", lambda session: setattr(next(actor for actor in session.combatants if actor.id == item.id), "actions", [action for action in next(actor for actor in session.combatants if actor.id == item.id).actions if action.id != action_id]), actor_id=item.id)

    def _refresh_encounter_targets(self, attacker: Combatant | None = None) -> None:
        if not hasattr(self, "encounter_target_box"):
            return
        previous_id = self._encounter_target_ids.get(self.encounter_target_var.get(), "")
        selected = self.encounter_action_tree.selection() if hasattr(self, "encounter_action_tree") else ()
        selected_action = next((entry for entry in attacker.actions if selected and entry.id == selected[0]), None) if attacker else None
        allow_self = bool(selected_action and selected_action.effect_type.casefold() == "healing")
        targets = [entry for entry in (self.current_encounter.ordered_combatants() if self.current_encounter else []) if (allow_self or not attacker or entry.id != attacker.id) and not entry.defeated]
        labels: list[str] = []
        target_ids: dict[str, str] = {}
        counts: dict[str, int] = {}
        for target in targets:
            base = f"{target.name} — {target.faction}"
            counts[base] = counts.get(base, 0) + 1
            label = base if counts[base] == 1 else f"{base} ({counts[base]})"
            labels.append(label)
            target_ids[label] = target.id
        self._encounter_target_ids = target_ids
        self.encounter_target_box.configure(values=labels)
        selected = next((label for label, target_id in target_ids.items() if target_id == previous_id), "")
        if not selected and labels:
            selected = labels[0]
        if self.encounter_target_var.get() != selected:
            self.encounter_target_var.set(selected)
            self._clear_encounter_resolution_preview()

    def _clear_encounter_resolution_preview(self) -> None:
        self._encounter_resolution_preview = None
        if hasattr(self, "encounter_resolve_status_var"):
            self.encounter_resolve_status_var.set("Choose an action and target.")
        if hasattr(self, "encounter_apply_button"):
            self.encounter_apply_button.configure(state="disabled")
            self.encounter_log_button.configure(state="disabled")

    @staticmethod
    def _encounter_die(value: str, label: str) -> int:
        if not value.strip():
            return random.randint(1, 20)
        result = int(value)
        if not 1 <= result <= 20:
            raise ValueError(f"{label} must be between 1 and 20.")
        return result

    def _encounter_preview_action(self) -> None:
        attacker = self._selected_encounter_combatant()
        action_selection = self.encounter_action_tree.selection()
        action = next((entry for entry in attacker.actions if action_selection and entry.id == action_selection[0]), None) if attacker else None
        if action and action.effect_type == "Special":
            available, required = RulesEngine.action_resource_status(attacker, action)
            if required and available < required:
                self.encounter_resolve_status_var.set(
                    f"{action.name}: insufficient {action.resource_name} ({available}/{required}).")
                return
            self._encounter_use_special_action(action.id)
            return
        target_id = self._encounter_target_ids.get(self.encounter_target_var.get(), "")
        if not attacker or not action_selection or not target_id or not self.current_encounter:
            self.encounter_resolve_status_var.set("Select an actor, action, and active target.")
            return
        action = next((entry for entry in attacker.actions if entry.id == action_selection[0]), None)
        target = next((entry for entry in self.current_encounter.combatants if entry.id == target_id and not entry.defeated), None)
        if not action or not target or (target.id == attacker.id and action.effect_type.casefold() != "healing"):
            self.encounter_resolve_status_var.set("The selected action or target is no longer available.")
            return
        available, required = RulesEngine.action_resource_status(attacker, action)
        if required and available < required:
            self._clear_encounter_resolution_preview()
            self.encounter_resolve_status_var.set(
                f"{action.name}: insufficient {action.resource_name} ({available}/{required}). Reload first.")
            return
        if action.effect_type.casefold() == "healing":
            self._encounter_resolution_preview = {
                "attacker_id": attacker.id, "target_id": target.id, "action_id": action.id,
                "attack": {"hit": True, "healing": True}, "resistance": {"suggestions": []}, "condition_triggers": {},
            }
            cure = "Cures Diseased" if action.persistent else "Healing is not Persistent"
            self.encounter_resolve_status_var.set(f"HEALING · {cure}")
            self.encounter_apply_button.configure(state="normal")
            self.encounter_log_button.configure(state="normal")
            return
        try:
            attack_die = self._encounter_die(self.encounter_attack_die_var.get(), "Attack die")
            resistance_die = self._encounter_die(self.encounter_resistance_die_var.get(), "Resistance die")
        except ValueError as exc:
            self._clear_encounter_resolution_preview()
            self.encounter_resolve_status_var.set(str(exc))
            return
        self.encounter_attack_die_var.set(str(attack_die))
        self.encounter_resistance_die_var.set(str(resistance_die))
        attack = RulesEngine.attack_result(attacker, target, action, attack_die)
        resistance = RulesEngine.resistance_result(target, action, resistance_die) if attack["hit"] else None
        condition_triggers = RulesEngine.condition_trigger_results(target, action) if attack["hit"] else {}
        self._encounter_resolution_preview = {
            "attacker_id": attacker.id, "target_id": target.id, "action_id": action.id,
            "attack": attack, "resistance": resistance, "condition_triggers": condition_triggers,
        }
        if resistance is None:
            self.encounter_resolve_status_var.set(
                f"MISS — attack {attack['total']} vs {attack['defense']}. Apply to spend ammo and log the miss.")
            self.encounter_apply_button.configure(state="normal")
        else:
            suggestions = ", ".join(str(value) for value in resistance["suggestions"]) or "No effect"
            trigger_text = ", ".join(
                f"{name} {'reset' if value.get('reset') else 'applies' if value.get('applied') else 'missed'}"
                for name, value in condition_triggers.items()
            )
            if trigger_text:
                suggestions = f"{suggestions} · {trigger_text}"
            self.encounter_resolve_status_var.set(
                f"HIT {attack['total']} vs {attack['defense']} · resistance {resistance['total']} vs DC {resistance['dc']} · degree {resistance['degree']} · {suggestions}"
            )
            self.encounter_apply_button.configure(state="normal")
        self.encounter_log_button.configure(state="normal")

    @staticmethod
    def _use_encounter_action_economy(actor: Combatant, action: ActionCard) -> None:
        action_kind = action.action_type.casefold()
        if action_kind == "standard":
            actor.standard_used = True
        elif action_kind == "move":
            actor.move_used = True
        elif action_kind == "reaction":
            actor.reaction_used = True
        else:
            actor.free_used += 1

    @staticmethod
    def _sync_encounter_inventory_loot(session: EncounterSession, actor: Combatant) -> None:
        character = actor.character()
        quantities = {item.id: item.quantity for item in character.equipment} if character else {}
        retained = []
        for loot in session.loot:
            if loot.source_combatant_id != actor.id or not loot.gear_identity:
                retained.append(loot)
                continue
            if loot.gear_identity in quantities:
                loot.quantity = quantities[loot.gear_identity]
                retained.append(loot)
            elif loot.revealed:
                loot.quantity = 0
                retained.append(loot)
        session.loot = retained

    def _encounter_consume_action_resource(self, action_id: str) -> None:
        attacker = self._selected_encounter_combatant()
        action = next((entry for entry in attacker.actions if entry.id == action_id), None) if attacker else None
        if not attacker or not action:
            return
        available, required = RulesEngine.action_resource_status(attacker, action)
        if not required:
            self.encounter_status_var.set(f"{action.name} has unlimited uses; no inventory was consumed.")
            return
        consumed = min(available, required)

        def operation(session: EncounterSession) -> None:
            actor = next(entry for entry in session.combatants if entry.id == attacker.id)
            current_action = next(entry for entry in actor.actions if entry.id == action.id)
            RulesEngine.consume_action_resource(actor, current_action)
            self._sync_encounter_inventory_loot(session, actor)
            self._use_encounter_action_economy(actor, current_action)

        warning = " (insufficient inventory)" if available < required else ""
        self._encounter_mutate(
            "Action", f"{attacker.name} used {action.name}; consumed {consumed}/{required} {action.resource_name}{warning}",
            operation, actor_id=attacker.id,
        )

    def _encounter_use_action_on_target(self, action_id: str, target_id: str, attacker_id: str = "") -> None:
        if not self.current_encounter:
            return
        attacker = next((entry for entry in self.current_encounter.combatants if entry.id == attacker_id), None) if attacker_id else self._selected_encounter_combatant()
        action = next((entry for entry in attacker.actions if entry.id == action_id), None) if attacker else None
        target_combatant = next((entry for entry in self.current_encounter.combatants if entry.id == target_id), None)
        target_objective = next((entry for entry in self.current_encounter.objectives if entry.id == target_id and entry.initiative is not None and entry.state == "Active"), None)
        target: Combatant | Objective | None = target_combatant or target_objective
        if not attacker or not action or not target:
            return

        available, required = RulesEngine.action_resource_status(attacker, action)
        if required and available < required:
            self.encounter_status_var.set(
                f"{action.name}: insufficient {action.resource_name} ({available}/{required}).")
            return
        attack = resistance = None
        if target_combatant is not None and action.effect_type.casefold() == "healing":
            attack = {"hit": True, "healing": True}
            resistance = {"suggestions": []}
        elif target_combatant is not None and action.effect_type != "Special":
            attack = RulesEngine.attack_result(attacker, target, action, random.randint(1, 20))
            resistance = RulesEngine.resistance_result(target, action, random.randint(1, 20)) if attack["hit"] else None
        trigger_results = RulesEngine.condition_trigger_results(target_combatant, action) if target_combatant is not None and resistance is not None and action.effect_type.casefold() != "healing" else {}
        hit_rolls: dict[str, dict[str, int]] = {}
        if target_combatant is not None and resistance is not None and action.effect_type.casefold() != "healing":
            hit_conditions = [entry for entry in target_combatant.conditions if entry.active and not entry.suppressed and entry.name in {"Bleeding", "Dying"}]
            prompted = self._prompt_condition_rolls(target_combatant, hit_conditions, "On-hit condition saves")
            if prompted is None:
                return
            hit_rolls = prompted
        applied_result: dict[str, object] = {}

        def operation(session: EncounterSession) -> None:
            actor = next(entry for entry in session.combatants if entry.id == attacker.id)
            current_action = next(entry for entry in actor.actions if entry.id == action.id)
            RulesEngine.consume_action_resource(actor, current_action)
            self._sync_encounter_inventory_loot(session, actor)
            self._use_encounter_action_economy(actor, current_action)
            if resistance is not None:
                current_target = next(entry for entry in session.combatants if entry.id == target.id)
                applied_result.update(RulesEngine.apply_action_hit(current_target, current_action, resistance, trigger_results, session.round, hit_rolls))
                if current_target.defeated:
                    reveal_loot_for(session, current_target.id)

        resource_note = ""
        if required:
            resource_note = f"; consumed {min(available, required)}/{required} {action.resource_name}"
            if available < required:
                resource_note += " (insufficient inventory)"
        if target_combatant is None:
            result_note = "logged against initiative label"
        elif action.effect_type == "Special":
            result_note = action.notes or "special action"
        elif resistance is None:
            result_note = f"missed ({attack['total']} vs {attack['defense']})"
        else:
            suggestions = ", ".join(str(value) for value in resistance["suggestions"]) or "no effect"
            result_note = f"hit; {suggestions}"
        dice = {"attack": attack, "resistance": resistance, "condition_triggers": trigger_results, "hit_condition_rolls": hit_rolls, "applied": applied_result} if attack is not None else None
        self._encounter_mutate(
            "Action", f"{attacker.name} used {action.name} on {target.name}: {result_note}{resource_note}",
            operation, actor_id=attacker.id, dice=dice,
        )

    def _encounter_use_special_action(self, action_id: str) -> None:
        attacker = self._selected_encounter_combatant()
        action = next((entry for entry in attacker.actions if entry.id == action_id), None) if attacker else None
        if not attacker or not action:
            return
        available, required = RulesEngine.action_resource_status(attacker, action)
        if required and available < required:
            self.encounter_status_var.set(
                f"{action.name}: insufficient {action.resource_name} ({available}/{required}).")
            return
        def operation(session: EncounterSession) -> None:
            actor = next(entry for entry in session.combatants if entry.id == attacker.id)
            current_action = next(entry for entry in actor.actions if entry.id == action.id)
            RulesEngine.consume_action_resource(actor, current_action)
            self._sync_encounter_inventory_loot(session, actor)
            self._use_encounter_action_economy(actor, current_action)
        resource_note = f"; consumed {min(available, required)}/{required} {action.resource_name}" if required else ""
        if required and available < required:
            resource_note += " (insufficient inventory)"
        self._encounter_mutate("Action", f"{attacker.name} used {action.name}: {action.notes or action.effect_type}{resource_note}", operation, actor_id=attacker.id)

    def _encounter_preview_entities(self):
        preview = self._encounter_resolution_preview
        if not preview or not self.current_encounter:
            return None
        attacker = next((entry for entry in self.current_encounter.combatants if entry.id == preview["attacker_id"]), None)
        target = next((entry for entry in self.current_encounter.combatants if entry.id == preview["target_id"]), None)
        action = next((entry for entry in attacker.actions if entry.id == preview["action_id"]), None) if attacker else None
        return (attacker, target, action) if attacker and target and action else None

    def _encounter_apply_action_preview(self) -> None:
        """Commit a preview once, including a missed attack's ammunition cost."""
        entities = self._encounter_preview_entities()
        preview = self._encounter_resolution_preview
        if not entities or not preview:
            self._clear_encounter_resolution_preview()
            return
        attacker, target, action = entities
        available, required = RulesEngine.action_resource_status(attacker, action)
        if required and available < required:
            self._clear_encounter_resolution_preview()
            self.encounter_resolve_status_var.set(
                f"{action.name}: only {available}/{required} {action.resource_name} available.")
            return
        resistance = preview["resistance"]
        attack = preview["attack"]
        hit_rolls: dict[str, dict[str, int]] = {}
        if resistance is not None and action.effect_type.casefold() != "healing":
            hit_conditions = [
                entry for entry in target.conditions
                if entry.active and not entry.suppressed and entry.name in {"Bleeding", "Dying"}
            ]
            prompted = self._prompt_condition_rolls(target, hit_conditions, "On-hit condition saves")
            if prompted is None:
                return
            hit_rolls = prompted
        trigger_results = preview.get("condition_triggers", {})
        suggestions = (
            ", ".join(str(value) for value in resistance["suggestions"]) or "No effect"
            if resistance is not None else "Missed"
        )
        applied_result: dict[str, object] = {}

        def operation(session: EncounterSession) -> None:
            actor = next(entry for entry in session.combatants if entry.id == attacker.id)
            current_action = next(entry for entry in actor.actions if entry.id == action.id)
            RulesEngine.consume_action_resource(actor, current_action)
            self._sync_encounter_inventory_loot(session, actor)
            self._use_encounter_action_economy(actor, current_action)
            if resistance is not None:
                current_target = next(entry for entry in session.combatants if entry.id == target.id)
                applied_result.update(RulesEngine.apply_action_hit(
                    current_target, current_action, resistance, trigger_results, session.round, hit_rolls))
                if current_target.defeated:
                    reveal_loot_for(session, current_target.id)

        ammo_note = f"; used {required} {action.resource_name}" if required else ""
        summary = f"{attacker.name} used {action.name} on {target.name}: {suggestions}{ammo_note}"
        self._encounter_mutate(
            "Resolution", summary, operation, actor_id=attacker.id,
            dice={"attack": attack, "resistance": resistance, "condition_triggers": trigger_results,
                  "hit_condition_rolls": hit_rolls, "applied": applied_result},
        )

    def _encounter_log_action_preview(self) -> None:
        entities = self._encounter_preview_entities()
        preview = self._encounter_resolution_preview
        if not entities or not preview:
            self._clear_encounter_resolution_preview()
            return
        attacker, target, action = entities
        attack = preview["attack"]
        resistance = preview["resistance"]
        summary = f"{attacker.name} missed {target.name} with {action.name}" if resistance is None else f"Calculated {action.name} vs {target.name}; result not applied"
        dice = {"attack": attack}
        if resistance is not None:
            dice["resistance"] = resistance
        self._encounter_mutate("Roll", summary, lambda _session: None, actor_id=attacker.id, dice=dice)

    def _encounter_resolve_action(self) -> None:
        if self._encounter_resolution_preview:
            self._encounter_apply_action_preview()
        else:
            self._encounter_preview_action()

    def _encounter_publish_actions(self) -> None:
        item = self._selected_encounter_combatant()
        if not item or not item.source_character_id:
            messagebox.showwarning("No source character", "This combatant is not linked to a saved character.")
            return
        for action in item.actions:
            if not RulesEngine.is_derived_action(action):
                self.encounter_store.save_action(item.source_character_id, action)
        self.encounter_status_var.set(f"Published reusable actions for {item.name}.")

    def _encounter_add_gear(self) -> None:
        item = self._selected_encounter_combatant()
        if not item or not item.character_snapshot or not self.snapshot:
            messagebox.showwarning("Cannot add gear", "Select a character combatant and wait for the catalog to load.")
            return

        window = tk.Toplevel(self.root)
        window.title(f"Add gear — {item.name}")
        window.geometry("720x480")
        window.transient(self.root)
        query = tk.StringVar()
        tree = ttk.Treeview(window, columns=("Category", "Manufacturer", "Price"), show="tree headings", selectmode="extended", style="Encounter.Treeview")
        tree.heading("#0", text="Gear")
        for column in ("Category", "Manufacturer", "Price"):
            tree.heading(column, text=column)
        by_id = {gear.id: gear for gear in self.snapshot.items}

        def refresh(*_args) -> None:
            needle = query.get().strip().casefold()
            tree.delete(*tree.get_children())
            for gear in self.snapshot.items:
                if needle and needle not in f"{gear.name} {gear.category} {gear.manufacturer} {gear.kind}".casefold():
                    continue
                tree.insert("", "end", iid=gear.id, text=gear.name, values=(gear.category, gear.manufacturer, f"{gear.price:g}"))

        search = ttk.Entry(window, textvariable=query)
        search.pack(fill="x", padx=8, pady=(8, 4))
        search.bind("<KeyRelease>", refresh)
        tree.pack(fill="both", expand=True, padx=8)

        def add() -> None:
            selected = [by_id[gear_id] for gear_id in tree.selection() if gear_id in by_id]
            if not selected:
                return
            self._encounter_add_gear_items(item, selected)
            window.destroy()

        row = ttk.Frame(window, padding=8)
        row.pack(fill="x")
        ttk.Button(row, text="Cancel", command=window.destroy).pack(side="right")
        ttk.Button(row, text="Add Selected", command=add, style="EncounterPrimary.TButton").pack(side="right", padx=(0, 4))
        tree.bind("<Double-1>", lambda _event: add())
        refresh()
        search.focus_set()

    def _encounter_add_gear_items(self, item: Combatant, selected: list[GearItem]) -> None:
        if not self.snapshot:
            return

        def operation(session: EncounterSession) -> None:
            actor = next(actor for actor in session.combatants if actor.id == item.id)
            character = actor.character()
            if not character:
                return
            settings = GeneratorSettings(race=character.race, budget=max(character.budget, character.spent + 10_000_000))
            rng = random.Random(random.SystemRandom().randrange(2**63))
            for gear in selected:
                forced = self.character_generator._force_item_prerequisites(character, gear, self.snapshot.items, settings, rng, ignore_limits=True)
                if forced is None:
                    continue
                selection = self.character_generator._selection(rng, gear, character, settings, self.snapshot.items, ignore_limits=True)
                if selection:
                    character.equipment.append(selection)
            character.spent = round(sum(gear.adjusted_price for gear in character.equipment), 2)
            character.budget = max(character.budget, character.spent)
            character.recalculate_corruption_penalties()
            actor.character_snapshot = character.to_dict()
            manual = [action for action in actor.actions if not RulesEngine.is_derived_action(action)]
            actor.actions = RulesEngine.derive_actions(actor) + manual
            stage_loot(session)

        self._encounter_mutate("Gear", f"Added catalog gear to {item.name}", operation, actor_id=item.id)

    def _encounter_remove_gear(self) -> None:
        item = self._selected_encounter_combatant()
        selected = self.encounter_gear_tree.selection()
        if not item or not selected or not item.character_snapshot:
            return
        gear_id = selected[0]

        def operation(session: EncounterSession) -> None:
            actor = next(actor for actor in session.combatants if actor.id == item.id)
            character = actor.character()
            if not character:
                return
            character.equipment = [gear for gear in character.equipment if gear.id != gear_id]
            session.loot = [loot for loot in session.loot if not (loot.source_combatant_id == actor.id and loot.gear_identity == gear_id and not loot.revealed)]
            character.spent = round(sum(gear.adjusted_price for gear in character.equipment), 2)
            character.recalculate_corruption_penalties()
            actor.character_snapshot = character.to_dict()
            manual = [action for action in actor.actions if not RulesEngine.is_derived_action(action)]
            actor.actions = RulesEngine.derive_actions(actor) + manual

        self._encounter_mutate("Gear", f"Removed gear from {item.name}", operation, actor_id=item.id)

    def _encounter_refresh_source(self) -> None:
        item = self._selected_encounter_combatant()
        source = self.character_store.get(item.source_character_id) if item and item.source_character_id else None
        if not item or not source:
            return
        changes = self.encounter_store.refresh_preview(item, source)
        if not changes:
            self.encounter_status_var.set("Source snapshot is already current.")
            return
        summary = "\n".join(f"{key}: changed" for key in list(changes)[:15])
        if messagebox.askyesno("Refresh source snapshot?", f"{len(changes)} top-level field(s) changed:\n{summary}\n\nReplace the frozen snapshot?"):
            self._encounter_mutate("Refresh", f"Refreshed {item.name} from saved source", lambda session: self.encounter_store.refresh_combatant(next(actor for actor in session.combatants if actor.id == item.id), source), actor_id=item.id)

    def _encounter_add_loot(self) -> None:
        if not self.current_encounter:
            return
        values = prompt_form(self.root, "Add loot", (
            FormField("name", "Item name"),
            FormField("quantity", "Quantity", "int", 1, minimum=1, maximum=999),
            FormField("value", "Unit value", "float", 0, minimum=0),
        ), submit_label="Add loot")
        if not values or not values["name"]:
            return
        loot = LootEntry(name=values["name"], quantity=values["quantity"], value=values["value"],
                         revealed=True, source_name="Manual")
        self._encounter_mutate("Loot", f"Added loot {loot.name}", lambda session: session.loot.append(loot))

    def _encounter_reveal_loot(self) -> None:
        selected = self.encounter_loot_tree.selection()
        if selected:
            self._encounter_mutate("Loot", "Revealed loot", lambda session: setattr(next(item for item in session.loot if item.id == selected[0]), "revealed", True))

    def _encounter_exclude_loot(self) -> None:
        selected = self.encounter_loot_tree.selection()
        if selected:
            self._encounter_mutate("Loot", "Toggled loot exclusion", lambda session: setattr(next(item for item in session.loot if item.id == selected[0]), "excluded", not next(item for item in session.loot if item.id == selected[0]).excluded))

    def _encounter_resource(self, amount: int) -> None:
        item = self._selected_encounter_combatant()
        selected = self.encounter_resource_tree.selection()
        if item and selected:
            resource_id = selected[0]
            self._encounter_mutate("Resource", f"Resource {amount:+d}", lambda session: setattr(next(resource for resource in next(actor for actor in session.combatants if actor.id == item.id).resources if resource.id == resource_id), "current", max(0, next(resource for resource in next(actor for actor in session.combatants if actor.id == item.id).resources if resource.id == resource_id).current + amount)), actor_id=item.id)

    def _encounter_add_resource(self) -> None:
        item = self._selected_encounter_combatant()
        if not item:
            return
        values = prompt_form(self.root, "Add resource", (
            FormField("name", "Resource name", value="Ammunition"),
            FormField("maximum", "Current / maximum", "int", 1, minimum=0, maximum=9999),
        ), submit_label="Add resource")
        if not values or not values["name"]:
            return
        resource = ResourceCounter(name=values["name"], current=values["maximum"], maximum=values["maximum"])
        self._encounter_mutate("Resource", f"Added resource {resource.name}",
            lambda session: next(actor for actor in session.combatants if actor.id == item.id).resources.append(resource), actor_id=item.id)

    def _encounter_add_objective(self) -> None:
        if not self.current_encounter:
            return
        values = prompt_form(self.root, "Add objective or hazard", (
            FormField("name", "Name"),
            FormField("kind", "Kind", "choice", "Objective", ("Objective", "Hazard", "Complication")),
            FormField("target", "Progress target", "int", 4, minimum=1),
            FormField("initiative", "Initiative (blank for none)", "optional_int", minimum=-100, maximum=200),
            FormField("timer", "Round timer (blank for none)", "optional_int", minimum=1, maximum=999),
        ), submit_label="Add")
        if not values or not values["name"]:
            return
        objective = Objective(name=values["name"], kind=values["kind"], target=values["target"],
                              initiative=values["initiative"], timer=values["timer"])
        self._encounter_mutate("Objective", f"Added {objective.kind} {objective.name}",
                               lambda session: session.objectives.append(objective))

    def _encounter_objective_progress(self, amount: int) -> None:
        selected = self.encounter_objective_tree.selection()
        if not selected:
            return
        objective_id = selected[0]

        def operation(session: EncounterSession) -> None:
            objective = next(item for item in session.objectives if item.id == objective_id)
            objective.progress = max(0, min(objective.target, objective.progress + amount))
            objective.state = "Complete" if objective.progress >= objective.target else "Active"

        self._encounter_mutate("Objective", f"Objective progress {amount:+d}", operation)

    def _encounter_add_environment(self) -> None:
        if not self.current_encounter:
            return
        selected = self.encounter_environment_tree.selection()
        if selected:
            modifier_id = selected[0]
            self._encounter_mutate("Environment", "Toggled environment modifier", lambda session: setattr(
                next(item for item in session.environment if item.id == modifier_id), "active",
                not next(item for item in session.environment if item.id == modifier_id).active))
            return
        zones = sorted({item.zone for item in self.current_encounter.combatants} | {"Global"})
        values = prompt_form(self.root, "Add environment modifier", (
            FormField("name", "Modifier name", value="Cover"),
            FormField("zone", "Zone", "choice", "Global", tuple(zones)),
            FormField("target", "Target defense/stat", "choice", "DODGE", tuple(dict.fromkeys((*DEFENSE_KEYS, *ABILITY_KEYS)))),
            FormField("amount", "Modifier amount", "int", 0, minimum=-20, maximum=20),
        ), submit_label="Add modifier")
        if not values or not values["name"]:
            return
        modifier = EnvironmentModifier(name=values["name"], zone=values["zone"], target=values["target"], amount=values["amount"])
        self._encounter_mutate("Environment", f"Added environment modifier {modifier.name}",
                               lambda session: session.environment.append(modifier))

    def _encounter_save_notes(self) -> None:
        item = self._selected_encounter_combatant()
        if item:
            notes = self.encounter_notes_text.get("1.0", "end-1c")
            self._encounter_mutate("Notes", f"Updated notes for {item.name}", lambda session: setattr(next(actor for actor in session.combatants if actor.id == item.id), "notes", notes), actor_id=item.id)

    def _encounter_undo(self) -> None:
        if self.encounter_controller and self.encounter_controller.undo():
            self._clear_encounter_resolution_preview()
            self.current_encounter = self.encounter_controller.session
            self._refresh_encounter_view()

    def _encounter_redo(self) -> None:
        if self.encounter_controller and self.encounter_controller.redo():
            self._clear_encounter_resolution_preview()
            self.current_encounter = self.encounter_controller.session
            self._refresh_encounter_view()

    def _complete_encounter(self) -> None:
        if not self.current_encounter:
            session = self.encounter_store.get_session(self._selected_session_id())
            if not session:
                return
            self._set_current_encounter(session)
        if not messagebox.askyesno("Complete encounter?", "Archive this encounter and generate its final summary?"):
            return
        publish = messagebox.askyesnocancel("Publish character changes?", "Publish current frozen character snapshots (including gear/stat changes) back to linked saved characters?\n\nYes = publish, No = archive only, Cancel = stop.")
        if publish is None:
            return
        if publish:
            for combatant in self.current_encounter.combatants:
                character = combatant.character()
                if character and combatant.source_character_id and messagebox.askyesno("Publish combatant?", f"Publish the frozen gear and derived character state for {combatant.name}?\n\nBruises, encounter conditions, and temporary modifiers are never written to the source character.", parent=self.root):
                    character.id = combatant.source_character_id
                    character.updated_at = datetime.now().astimezone().isoformat()
                    self.character_store.save(character)
        recover = messagebox.askyesno("Recover archived snapshots?", "Clear bruises and temporary conditions in the archived encounter? Final events and summary remain recorded.")

        def operation(session: EncounterSession) -> None:
            session.status = "Archived"
            if recover:
                for combatant in session.combatants:
                    combatant.bruises = 0
                    for condition in combatant.conditions:
                        RulesEngine.deactivate_condition(combatant, condition)
                    combatant.conditions.clear()
            session.notes += "\n\n" + summary_document(session).text

        self._encounter_mutate("Completion", "Archived encounter", operation)
        campaign = self.encounter_store.get_campaign(self.current_encounter.campaign_id) if self.current_encounter.campaign_id else None
        if campaign:
            campaign.chronology.append(f"{datetime.now().astimezone().date().isoformat()}: {self.current_encounter.name} ({self.current_encounter.round} rounds)")
            campaign.updated_at = datetime.now().astimezone().isoformat()
            self.encounter_store.save_campaign(campaign)
        self._refresh_encounter_library()

    def _print_encounter_initiative(self) -> None:
        if self.current_encounter:
            self.spooler.enqueue([initiative_document(self.current_encounter)], 1)

    def _print_encounter_combatant(self) -> None:
        item = self._selected_encounter_combatant()
        if item and self.current_encounter:
            self.spooler.enqueue([combatant_document(self.current_encounter, item)], 1)

    def _print_encounter_action(self) -> None:
        item = self._selected_encounter_combatant()
        selected = self.encounter_action_tree.selection()
        action = next((entry for entry in item.actions if selected and entry.id == selected[0]), None) if item else None
        if item and action:
            self.spooler.enqueue([action_document(item, action)], 1)

    def _print_encounter_condition(self) -> None:
        item = self._selected_encounter_combatant()
        selected = self.encounter_condition_tree.selection()
        condition = next((entry for entry in item.conditions if selected and entry.id == selected[0]), None) if item else None
        if item and condition:
            self.spooler.enqueue([condition_document(item, condition)], 1)

    def _print_encounter_loot(self) -> None:
        if self.current_encounter:
            self.spooler.enqueue([loot_document(self.current_encounter)], 1)

    def _print_encounter_summary(self) -> None:
        if self.current_encounter:
            self.spooler.enqueue([summary_document(self.current_encounter)], 1)

    def _encounter_focus_window(self) -> None:
        if not self.current_encounter:
            return
        window = tk.Toplevel(self.root)
        window.title(self.current_encounter.name)
        window.geometry("900x650")
        tree = ttk.Treeview(window, columns=("Init", "Faction", "Bruises", "State"), show="tree headings")
        tree.heading("#0", text="Combatant")
        for column in ("Init", "Faction", "Bruises", "State"):
            tree.heading(column, text=column)
        tree.pack(fill="both", expand=True, padx=10, pady=10)
        for item in self.current_encounter.ordered_combatants():
            state = "Defeated" if item.defeated else ", ".join(RulesEngine.active_conditions(item)) or "Ready"
            tree.insert("", "end", text=item.name, values=(item.initiative if item.initiative is not None else "—", item.faction, item.bruises, state))
        ttk.Label(window, text=f"Round {self.current_encounter.round}", font=("TkDefaultFont", 16, "bold")).pack(pady=(0, 10))

    def _character_settings(self) -> GeneratorSettings | None:
        try:
            settings = GeneratorSettings(
                power_level=int(self.char_pl_var.get()),
                role=self.char_role_var.get(),
                race=self.char_race_var.get(),
                budget=float(self.char_budget_var.get()),
                quality=float(self.char_quality_var.get()),
                quantity=float(self.char_quantity_var.get()),
                quality_enabled=bool(self.char_quality_enabled_var.get()),
                quantity_enabled=bool(self.char_quantity_enabled_var.get()),
                catalog_adds_ignore_limits=bool(self.char_catalog_ignore_limits_var.get()),
                weapon_mode=self.char_weapon_var.get(),
                guarantee_weapon=bool(self.char_guarantee_weapon_var.get()),
                affinity_strength=float(self.char_affinity_var.get()),
                category_initial={key: float(value.get()) for key, value in self.category_initial_vars.items()},
                category_continue={key: float(value.get()) for key, value in self.category_continue_vars.items()},
                category_max={key: int(value.get()) for key, value in self.category_max_vars.items()},
                race_enabled=dict(self.race_enabled),
                race_weights=dict(self.race_weights),
                parent_enabled=dict(self.parent_enabled),
                parent_weights=dict(self.parent_weights),
            )
            return settings.normalized()
        except (ValueError, tk.TclError) as exc:
            messagebox.showwarning("Invalid generator settings", f"Use numeric values for PL, budget, probabilities, and limits.\n\n{exc}")
            return None

    def _toggle_character_advanced(self) -> None:
        self.char_advanced_visible = not self.char_advanced_visible
        if self.char_advanced_visible:
            self.char_advanced_frame.pack(fill="x", padx=2, pady=(0, 6), before=self.character_panes)
        else:
            self.char_advanced_frame.pack_forget()

    def _set_character_budget(self, amount: float) -> str:
        self.char_budget_var.set(amount)
        return "break"

    def _update_qq_controls(self) -> None:
        if hasattr(self, "char_quality_scale"):
            self.char_quality_scale.configure(state="normal" if self.char_quality_enabled_var.get() else "disabled")
        if hasattr(self, "char_quantity_scale"):
            self.char_quantity_scale.configure(state="normal" if self.char_quantity_enabled_var.get() else "disabled")

    def _generate_character(self) -> None:
        if not self.snapshot:
            messagebox.showwarning("Catalog unavailable", "Wait for the first successful catalog refresh.")
            return
        settings = self._character_settings()
        if not settings:
            return
        try:
            seed = int(self.char_seed_var.get()) if self.char_seed_var.get().strip() else None
            requested_race = settings.race
            requested_role = settings.role
            self.current_character = self.character_generator.generate(self.snapshot, settings, name=self.char_name_var.get(), seed=seed)
        except ValueError as exc:
            messagebox.showwarning("Cannot generate", str(exc))
            return
        self.char_seed_var.set(str(self.current_character.seed))
        self._load_character_into_editor(self.current_character)
        if requested_race == "Random":
            self.char_race_var.set("Random")
        if requested_role == "Random":
            self.char_role_var.set("Random")
        self.char_status_var.set(f"Generated seed {self.current_character.seed}; {len(self.current_character.equipment)} items selected.")

    def _generate_new_seed(self) -> None:
        self.char_seed_var.set("")
        self._generate_character()

    def _load_character_into_editor(self, character: Character) -> None:
        self.current_character = character
        self.expanded_character_items.clear()
        self.essential_character_items.clear()
        self.character_physical_profile_expanded = False
        self.char_name_var.set(character.name)
        self.char_pl_var.set(character.power_level)
        self.char_role_var.set(character.role)
        self.char_race_var.set(character.race)
        self.char_budget_var.set(character.budget)
        self.char_seed_var.set(str(character.seed))
        for key in ABILITY_KEYS:
            self.ability_vars[key].set(character.base_abilities.get(key, 0))
        for key in DEFENSE_KEYS:
            self.defense_vars[key].set(character.defense_training.get(key, 0))
        self._refresh_character_view(preserve_scroll=False)

    def _refresh_character_view(self, *, preserve_scroll: bool = True) -> None:
        character = self.current_character
        selected_ids = set(self.character_equipment_tree.selection())
        preview_scroll = self.character_preview.yview()[0] if preserve_scroll else 0.0
        equipment_scroll = self.character_equipment_tree.yview()[0] if preserve_scroll else 0.0
        self.expanded_character_items.intersection_update(item.id for item in character.equipment) if character else self.expanded_character_items.clear()
        self.essential_character_items.intersection_update(item.id for item in character.equipment) if character else self.essential_character_items.clear()
        text = character_text(character, audit=True, expanded_item_ids=self.expanded_character_items, essential_item_ids=self.essential_character_items, physical_profile_expanded=self.character_physical_profile_expanded, show_physical_profile_toggle=True) if character else ""
        self.character_preview.configure(state="normal")
        self.character_preview.delete("1.0", "end")
        self.character_preview.insert("1.0", text)
        for index, (kind, start, end) in enumerate(toughness_token_spans(text)):
            tag = f"character-toughness-{kind}-{index}"
            self.character_preview.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")
            self.character_preview.tag_configure(tag, foreground=TOUGHNESS_COLORS[kind])
        if character:
            search_from = "1.0"
            displayed_items = [item for section in SECTIONS for item in character.equipment if item.category == section]
            for item in displayed_items:
                quantity = f" ×{item.quantity}" if item.quantity != 1 else ""
                needle = f"{item.name}{quantity} ["
                start = self.character_preview.search(needle, search_from, stopindex="end", exact=True)
                if not start:
                    continue
                name_start = start
                name_end = self.character_preview.index(f"{name_start}+{len(item.name)}c")
                tag = f"character-item-{item.id}"
                self.character_preview.tag_add(tag, name_start, name_end)
                color = "#d97706" if item.id in self.essential_character_items else "#1769aa"
                self.character_preview.tag_configure(tag, foreground=color, underline=True)
                self.character_preview.tag_bind(tag, "<Enter>", lambda _event: self.character_preview.configure(cursor="hand2"))
                self.character_preview.tag_bind(tag, "<Leave>", lambda _event: self.character_preview.configure(cursor=""))
                self.character_preview.tag_bind(tag, "<Button-1>", lambda _event, item_id=item.id: self._toggle_character_item_details(item_id))
                self.character_preview.tag_bind(tag, "<Button-3>", lambda _event, item_id=item.id: self._toggle_character_item_essential(item_id))
                search_from = name_end
            profile_start = self.character_preview.search("Physical Profile:", "1.0", stopindex="end", exact=True)
            if profile_start:
                profile_end = self.character_preview.index(f"{profile_start}+{len('Physical Profile:')}c")
                self.character_preview.tag_add("character-physical-profile", profile_start, profile_end)
                self.character_preview.tag_configure("character-physical-profile", foreground="#1769aa", underline=True)
                self.character_preview.tag_bind("character-physical-profile", "<Enter>", lambda _event: self.character_preview.configure(cursor="hand2"))
                self.character_preview.tag_bind("character-physical-profile", "<Leave>", lambda _event: self.character_preview.configure(cursor=""))
                self.character_preview.tag_bind("character-physical-profile", "<Button-1>", lambda _event: self._toggle_character_physical_profile())
        self.character_preview.configure(state="disabled")
        self.character_preview.yview_moveto(preview_scroll)
        self.character_equipment_tree.delete(*self.character_equipment_tree.get_children())
        if not character:
            return
        for item in character.equipment:
            quantity = f" ×{item.quantity}" if item.quantity != 1 else ""
            self.character_equipment_tree.insert("", "end", iid=item.id, values=(item.category, f"{item.name}{quantity}", item.manufacturer, f"{item.adjusted_price:g}", "Yes" if item.locked else ""))
        restore = [item_id for item_id in selected_ids if self.character_equipment_tree.exists(item_id)]
        if restore:
            self.character_equipment_tree.selection_set(restore)
        self.character_equipment_tree.yview_moveto(equipment_scroll)

    def _recalculate_character(self) -> None:
        if not self.current_character:
            return
        try:
            self.current_character.name = self.char_name_var.get().strip()
            chosen_race = self.char_race_var.get()
            if chosen_race != "Random":
                parent = race_parent(chosen_race)
                if not self.parent_enabled.get(parent, False) or self.parent_weights.get(parent, 0) <= 0 or not self.race_enabled.get(chosen_race, False) or self.race_weights.get(chosen_race, 0) <= 0:
                    raise ValueError("The selected race is excluded.")
                self.current_character.race = chosen_race
            self.current_character.base_abilities = {key: int(value.get()) for key, value in self.ability_vars.items()}
            self.current_character.defense_training = {key: int(value.get()) for key, value in self.defense_vars.items()}
            self.current_character.budget = float(self.char_budget_var.get())
        except (ValueError, tk.TclError):
            messagebox.showwarning("Invalid stats", "Base abilities and defense training must be whole numbers.")
            return
        self.current_character.spent = round(sum(item.adjusted_price for item in self.current_character.equipment), 2)
        self.current_character.recalculate_corruption_penalties()
        self.current_character.updated_at = datetime.now().astimezone().isoformat()
        self._refresh_character_view()
        used, capacity = self.current_character.bionic_capacity()
        cyberpsychosis = self.current_character.cyberpsychosis_points()
        suffix = f" — CyberPsychosis {cyberpsychosis} (-{cyberpsychosis * 5} all defenses)" if cyberpsychosis else ""
        self.char_status_var.set(f"Recalculated. Bionics {used:g}/{capacity:g}{suffix}")

    def _selected_character_item(self):
        if not self.current_character:
            return None
        selection = self.character_equipment_tree.selection()
        return next((item for item in self.current_character.equipment if selection and item.id == selection[0]), None)

    def _toggle_character_item_details(self, item_id: str):
        if item_id in self.expanded_character_items:
            self.expanded_character_items.remove(item_id)
        else:
            self.expanded_character_items.add(item_id)
        self._refresh_character_view()
        if self.character_equipment_tree.exists(item_id):
            self.character_equipment_tree.selection_set(item_id)

    def _toggle_character_physical_profile(self) -> None:
        self.character_physical_profile_expanded = not self.character_physical_profile_expanded
        self._refresh_character_view()
        return "break"

    def _toggle_character_item_essential(self, item_id: str):
        if item_id in self.essential_character_items:
            self.essential_character_items.remove(item_id)
            self.char_status_var.set("Item restored to normal detailed printing (blue).")
        else:
            self.essential_character_items.add(item_id)
            self.char_status_var.set("Item set to essential-info printing (orange).")
        self._refresh_character_view()
        if self.character_equipment_tree.exists(item_id):
            self.character_equipment_tree.selection_set(item_id)
        return "break"

    def _toggle_selected_character_item_details(self):
        item = self._selected_character_item()
        return self._toggle_character_item_details(item.id) if item else None

    def _toggle_all_character_items(self) -> None:
        if not self.current_character:
            return
        all_ids = {item.id for item in self.current_character.equipment}
        self.expanded_character_items = set() if all_ids and all_ids <= self.expanded_character_items else all_ids
        self._refresh_character_view()

    def _toggle_orange_character_items(self) -> None:
        if not self.current_character:
            return
        valid_ids = {item.id for item in self.current_character.equipment}
        orange_ids = self.essential_character_items & valid_ids
        if orange_ids and orange_ids <= self.expanded_character_items:
            self.expanded_character_items -= orange_ids
            action = "Collapsed"
        else:
            # A mixed group favors opening: the first press synchronizes all
            # orange tags open, and the next press synchronizes them closed.
            self.expanded_character_items |= orange_ids
            action = "Expanded"
        self._refresh_character_view()
        self.char_status_var.set(f"{action} {len(orange_ids)} orange item(s).")

    def _make_all_character_items_orange(self) -> None:
        if not self.current_character:
            return
        self.essential_character_items = {item.id for item in self.current_character.equipment}
        self._refresh_character_view()
        self.char_status_var.set(f"Marked all {len(self.essential_character_items)} item(s) orange.")

    def _show_encounter_hotkeys(self) -> None:
        messagebox.showinfo(
            "Encounter Tracker hotkeys",
            "\n".join(f"{shortcut:<18} {description}" for shortcut, description in ENCOUNTER_HOTKEYS),
            parent=self.root,
        )

    def _encounter_hotkey_allowed(self) -> bool:
        if getattr(self, "_condition_roll_prompt_active", False):
            return False
        if self.notebook.select() != str(self.encounter_tab):
            return False
        focus = self.root.focus_get()
        return not (focus and focus.winfo_class() in {"Entry", "TEntry", "Text", "Spinbox", "TSpinbox", "TCombobox"})

    def _encounter_hotkey(self, event=None):
        if event is None or not self._encounter_hotkey_allowed():
            return None
        key = event.keysym.casefold()
        shift = bool(event.state & 0x0001)
        control = bool(event.state & 0x0004)
        if control:
            command = {"z": self._encounter_undo, "y": self._encounter_redo}.get(key)
        else:
            command = {
                "space": self._encounter_next_turn,
                "i": self._encounter_roll_initiative,
                "s": self._encounter_set_initiative,
                "return": self._encounter_resolve_action,
                "kp_enter": self._encounter_resolve_action,
                "b": lambda: self._encounter_bruise(-1 if shift else 1),
                "c": self._encounter_add_condition,
                "d": self._encounter_toggle_defeated,
                "r": lambda: self._toggle_encounter_flag("readied"),
                "h": lambda: self._toggle_encounter_flag("holding"),
                "k": lambda: self._toggle_encounter_flag("skipped"),
                "1": lambda: self._toggle_encounter_flag("standard_used"),
                "2": lambda: self._toggle_encounter_flag("move_used"),
                "3": lambda: self._toggle_encounter_flag("reaction_used"),
                "l": self._encounter_reveal_loot,
                "o": lambda: self._encounter_objective_progress(1),
                "f6": lambda: self.encounter_tree.focus_set(),
                "f7": lambda: self.encounter_target_box.focus_set(),
                "escape": lambda: (self._cancel_encounter_action_drag(), self._clear_encounter_resolution_preview()),
                "f1": self._show_encounter_hotkeys,
            }.get(key)
        if command is None:
            return None
        command()
        return "break"

    def _character_hotkey_allowed(self) -> bool:
        if self.notebook.select() != str(self.character_tab):
            return False
        focus = self.root.focus_get()
        return not (
            focus
            and focus is not self.character_preview
            and focus.winfo_class() in {"Entry", "TEntry", "Text", "Spinbox", "TSpinbox"}
        )

    def _expand_orange_hotkey(self, _event=None):
        # Preserve normal typing in editable controls. The character preview is
        # a disabled Text widget, so it remains eligible for shortcuts.
        if not self._character_hotkey_allowed():
            return None
        self._toggle_orange_character_items()
        return "break"

    def _all_orange_hotkey(self, _event=None):
        if not self._character_hotkey_allowed():
            return None
        self._make_all_character_items_orange()
        return "break"

    @staticmethod
    def _equipment_as_gear(item) -> GearItem:
        source_category = {
            "Weapons": "Weapons",
            "Armor": "Apparel",
            "Bionics": "Bionics",
            "Genemods": "Gene Treatments",
            "Inventory": "Consumables/Gear",
        }[item.category]
        fields = dict(item.fields)
        fields["Manufacturer"] = item.manufacturer
        fields["Price"] = f"{item.adjusted_price:g}"
        kind = (
            fields.get("Form Factor", "") if source_category == "Weapons"
            else fields.get("Coverage", "") if source_category == "Apparel"
            else "Gene Treatment" if source_category == "Gene Treatments"
            else fields.get("Type", "")
        )
        return GearItem(
            category=source_category,
            source_gid=item.source_gid,
            source_row=item.source_row,
            fields=tuple(fields.items()),
            name=item.name,
            manufacturer=item.manufacturer,
            kind=kind,
            rank=fields.get("Rank", ""),
            price=fields.get("Price", ""),
        )

    def _print_selected_character_items(self) -> None:
        if not self.current_character:
            return
        selected_ids = set(self.character_equipment_tree.selection())
        selected = [item for item in self.current_character.equipment if item.id in selected_ids]
        if not selected:
            messagebox.showwarning("No generated items selected", "Select one or more generated items first.")
            return
        printable = [
            PrintDocument(item.name, item.category, essential_item_text(item))
            if item.id in self.essential_character_items
            else self._equipment_as_gear(item)
            for item in selected
        ]
        self.spooler.enqueue(printable, 1)
        self.char_status_var.set(f"Queued {len(selected)} standalone item tag(s).")

    def _choose_catalog_items(self, title: str) -> list[GearItem]:
        """Open a searchable picker in the current workflow without switching tabs."""
        if not self.snapshot:
            messagebox.showwarning("Catalog unavailable", "The gear catalog has not loaded yet.", parent=self.root)
            return []
        return choose_records(
            self.root, title, self.snapshot.items,
            (("Name", lambda gear: gear.name, 270),
             ("Category", lambda gear: gear.category, 120),
             ("Manufacturer", lambda gear: gear.manufacturer, 155),
             ("Price", lambda gear: f"{gear.price:g}", 90)),
            multiple=True, submit_label="Add selected gear",
        )

    def _add_catalog_item(self, chosen: list[GearItem] | None = None) -> None:
        if not self.current_character:
            messagebox.showwarning("No character", "Generate or load a character first.")
            return
        selected = self._selected_items() if chosen is None else chosen
        if not selected:
            return
            return
        settings = self._character_settings()
        if not settings:
            return
        rng = random.Random(random.SystemRandom().randrange(2**63))
        added = 0
        forced = 0
        prerequisite_skips = 0
        selection_skips = 0
        catalog_items = self.snapshot.items if self.snapshot else ()
        ignore_limits = bool(self.char_catalog_ignore_limits_var.get())
        for gear in selected:
            forced_items = []
            if gear.category == "Bionics":
                forced_result = self.character_generator._force_item_prerequisites(
                    self.current_character, gear, catalog_items, settings, rng,
                    ignore_limits=ignore_limits,
                )
                if forced_result is None:
                    prerequisite_skips += 1
                    continue
                forced_items = forced_result
            elif gear.category == "Apparel" and not self.character_generator._prerequisites_met(gear, self.current_character):
                prerequisite_skips += 1
                continue
            item = self.character_generator._selection(
                rng, gear, self.current_character, settings, catalog_items,
                ignore_limits=ignore_limits,
            )
            if item is None:
                self.character_generator._rollback_forced_items(self.current_character, forced_items)
                selection_skips += 1
                continue
            self.current_character.equipment.append(item)
            self.current_character.spent += item.adjusted_price
            forced += len(forced_items)
            added += 1
        raised_budget = False
        if self.current_character.spent > self.current_character.budget:
            self.current_character.budget = round(self.current_character.spent, 2)
            self.char_budget_var.set(self.current_character.budget)
            raised_budget = True
        self._recalculate_character()
        details = [f"Added {added} selected catalog item(s)"]
        if forced:
            details.append(f"{forced} forced prerequisite(s)")
        if prerequisite_skips:
            details.append(f"{prerequisite_skips} skipped for unmet prerequisites")
        if selection_skips:
            details.append(f"{selection_skips} skipped for budget, price, or eligibility")
        if raised_budget:
            details.append(f"budget increased to {self.current_character.budget:g}")
        self.char_status_var.set("; ".join(details) + ".")

    def _remove_character_item(self) -> None:
        item = self._selected_character_item()
        if not item or not self.current_character:
            return
        if item.locked:
            messagebox.showwarning("Item locked", "Unlock this item before removing it.")
            return
        self.current_character.equipment.remove(item)
        self._recalculate_character()

    def _toggle_character_item_lock(self) -> None:
        item = self._selected_character_item()
        if item:
            item.locked = not item.locked
            self._refresh_character_view()

    def _catalog_item_for_selection(self, item) -> GearItem | None:
        if not self.snapshot:
            return None
        return next((gear for gear in self.snapshot.items if gear.source_gid == item.source_gid and gear.source_row == item.source_row), None)

    def _reroll_character_item(self) -> None:
        old = self._selected_character_item()
        if not old or not self.current_character or old.locked:
            return
        gear = self._catalog_item_for_selection(old)
        settings = self._character_settings()
        if not gear or not settings:
            messagebox.showwarning("Cannot reroll", "The source catalog row is unavailable.")
            return
        current_index = self.current_character.equipment.index(old)
        self.current_character.equipment.pop(current_index)
        replacement = self.character_generator._selection(random.Random(random.SystemRandom().randrange(2**63)), gear, self.current_character, settings, self.snapshot.items if self.snapshot else ())
        if replacement:
            self.current_character.equipment.insert(current_index, replacement)
        else:
            self.current_character.equipment.insert(current_index, old)
        if replacement is None:
            messagebox.showwarning("Cannot reroll", "No eligible manufacturer result fit the current character and budget.")
        elif replacement.category == "Genemods":
            self.current_character.corruption_penalties = []
        self._recalculate_character()

    def _reroll_character_section(self) -> None:
        selected = self._selected_character_item()
        if not selected or not self.current_character or not self.snapshot:
            return
        settings = self._character_settings()
        if not settings:
            return
        settings.race = self.current_character.race
        replacement_character = self.character_generator.generate(self.snapshot, settings, name=self.current_character.name)
        kept = [item for item in self.current_character.equipment if item.category != selected.category or item.locked]
        replacements = [item for item in replacement_character.equipment if item.category == selected.category]
        self.current_character.equipment = kept + replacements
        if selected.category == "Genemods":
            self.current_character.corruption_penalties = list(replacement_character.corruption_penalties)
        self._recalculate_character()
        self.char_status_var.set(f"Rerolled {selected.category}; locked items were retained.")

    def _refresh_saved_characters(self) -> None:
        if not hasattr(self, "saved_character_tree"):
            return
        selected = self.saved_character_tree.selection()
        self.saved_character_tree.delete(*self.saved_character_tree.get_children())
        for character in self.character_store.list(self.char_search_var.get()):
            self.saved_character_tree.insert("", "end", iid=character.id, values=(character.name or "—", character.race, character.role, character.power_level))
        if selected and self.saved_character_tree.exists(selected[0]):
            self.saved_character_tree.selection_set(selected[0])

    def _selected_saved_id(self) -> str | None:
        selection = self.saved_character_tree.selection()
        return selection[0] if selection else None

    def _load_saved_character(self) -> None:
        character_id = self._selected_saved_id()
        character = self.character_store.get(character_id) if character_id else None
        if character:
            self._load_character_into_editor(character)
            self.char_status_var.set("Loaded saved snapshot; live catalog changes have not altered it.")

    def _save_character(self) -> None:
        if not self.current_character:
            messagebox.showwarning("No character", "Generate or load a character first.")
            return
        self._recalculate_character()
        self.character_store.save(self.current_character)
        self._refresh_saved_characters()
        self.char_status_var.set("Character saved.")

    def _duplicate_character(self) -> None:
        if not self.current_character:
            return
        copy = Character.from_dict(self.current_character.to_dict())
        copy.id = str(uuid4())
        copy.name = f"{copy.name} Copy".strip()
        now = datetime.now().astimezone().isoformat()
        copy.created_at = copy.updated_at = now
        self.character_store.save(copy)
        self._load_character_into_editor(copy)
        self._refresh_saved_characters()

    def _delete_character(self) -> None:
        character_id = self._selected_saved_id()
        if character_id and messagebox.askyesno("Delete character?", "Delete the selected saved character permanently?"):
            self.character_store.delete(character_id)
            self._refresh_saved_characters()

    def _print_character(self) -> None:
        if not self.current_character:
            messagebox.showwarning("No character", "Generate or load a character first.")
            return
        self._recalculate_character()
        label = self.current_character.name or self.current_character.race
        self.spooler.enqueue([
            PrintDocument(
                label,
                "Character",
                character_text(
                    self.current_character,
                    expanded_item_ids=self.expanded_character_items,
                    essential_item_ids=self.essential_character_items,
                    physical_profile_expanded=self.character_physical_profile_expanded,
                ),
            )
        ], 1)
        self.char_status_var.set("Character receipt queued in the shared print spooler.")

    def _open_race_editor(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Race probabilities")
        window.geometry("620x620")
        columns = ("Enabled", "Weight")
        tree = ttk.Treeview(window, columns=columns, show="tree headings", selectmode="extended")
        tree.heading("#0", text="Parent race / subrace")
        tree.column("#0", width=380, stretch=True)
        for column, width in (("Enabled", 70), ("Weight", 90)):
            tree.heading(column, text=column)
            tree.column(column, width=width, stretch=False)
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Label(window, text="Parent weights are rolled first. Expanded child weights are conditional on that parent winning.").pack(fill="x", padx=8, pady=(0, 5))

        def refresh() -> None:
            open_parents = {iid for iid in tree.get_children() if tree.item(iid, "open")}
            selected = set(tree.selection())
            tree.delete(*tree.get_children())
            race_indices = {race.name: index for index, race in enumerate(RACES)}
            for parent, races in RACE_GROUPS.items():
                parent_iid = f"p:{parent}"
                if len(races) == 1:
                    race = races[0]
                    enabled = self.parent_enabled[parent] and self.race_enabled[race.name]
                    tree.insert("", "end", iid=parent_iid, text=parent, values=("Yes" if enabled else "No", f"{self.parent_weights[parent]:g}"))
                else:
                    tree.insert("", "end", iid=parent_iid, text=parent, values=("Yes" if self.parent_enabled[parent] else "No", f"{self.parent_weights[parent]:g}"), open=parent_iid in open_parents)
                    for race in races:
                        child_iid = f"r:{race_indices[race.name]}"
                        label = race.name.split(":", 1)[1].strip()
                        tree.insert(parent_iid, "end", iid=child_iid, text=label, values=("Yes" if self.race_enabled[race.name] else "No", f"{self.race_weights[race.name]:g}"))
            restore = [iid for iid in selected if tree.exists(iid)]
            if restore:
                tree.selection_set(restore)

        def set_enabled(value: bool) -> None:
            targets = tree.selection() or tree.get_children()
            for iid in targets:
                self._set_race_tree_enabled(iid, value)
            refresh()

        def set_weight() -> None:
            try:
                value = max(0.0, float(weight_var.get()))
            except ValueError:
                return
            for iid in tree.selection():
                if iid.startswith("p:"):
                    self.parent_weights[iid[2:]] = value
                elif iid.startswith("r:"):
                    self.race_weights[RACES[int(iid[2:])].name] = value
            refresh()

        actions = ttk.Frame(window, padding=(8, 0, 8, 8))
        actions.pack(fill="x")
        ttk.Button(actions, text="Enable selected/all", command=lambda: set_enabled(True)).pack(side="left")
        ttk.Button(actions, text="Exclude selected/all", command=lambda: set_enabled(False)).pack(side="left", padx=4)
        weight_var = tk.StringVar(value="1")
        ttk.Entry(actions, textvariable=weight_var, width=8).pack(side="left", padx=(12, 3))
        ttk.Button(actions, text="Set weight", command=set_weight).pack(side="left")
        ttk.Button(actions, text="Reset defaults", command=lambda: self._reset_races(refresh)).pack(side="left", padx=8)
        ttk.Button(actions, text="Done", command=lambda: self._close_race_editor(window)).pack(side="right")
        tree.bind("<Double-1>", lambda _event: self._toggle_race_from_tree(tree, refresh))
        refresh()

    def _toggle_race_from_tree(self, tree: ttk.Treeview, refresh) -> None:
        for iid in tree.selection():
            if iid.startswith("p:"):
                parent = iid[2:]
                self._set_race_tree_enabled(iid, not self.parent_enabled[parent])
            elif iid.startswith("r:"):
                name = RACES[int(iid[2:])].name
                self.race_enabled[name] = not self.race_enabled[name]
        refresh()

    def _set_race_tree_enabled(self, iid: str, value: bool) -> None:
        if iid.startswith("p:"):
            parent = iid[2:]
            self.parent_enabled[parent] = value
            if len(RACE_GROUPS[parent]) == 1:
                self.race_enabled[RACE_GROUPS[parent][0].name] = value
        elif iid.startswith("r:"):
            self.race_enabled[RACES[int(iid[2:])].name] = value

    def _reset_races(self, refresh) -> None:
        self.race_enabled = {race.name: True for race in RACES}
        self.race_weights = {race.name: race.default_weight for race in RACES}
        self.parent_enabled = {parent: True for parent in RACE_GROUPS}
        self.parent_weights = {parent: 1.0 for parent in RACE_GROUPS}
        refresh()

    def _cached_catalog_status(self, prefix: str = "Catalog: cached") -> str:
        if not self.snapshot:
            return "Catalog: unavailable"
        age = max(0, int((datetime.now().astimezone() - self.snapshot.fetched_at.astimezone()).total_seconds()))
        if age < 60:
            label = "less than a minute old"
        elif age < 3600:
            label = f"{age // 60} minute(s) old"
        elif age < 86400:
            label = f"{age // 3600} hour(s) old"
        else:
            label = f"{age // 86400} day(s) old"
        return f"{prefix} — {label}"

    def _show_storage_recovery_warning(self) -> None:
        messagebox.showerror(
            "Local data unavailable",
            "The application could not open its normal character database and is using temporary "
            "storage for this session. Do not save new work until the problem is resolved.\n\n"
            f"Error: {self.storage_error}\n\n"
            "Use Data Management to restore a valid backup. Diagnostic details were written to the log.",
            parent=self.root,
        )

    def _report_callback_exception(self, exception_type, exception, traceback) -> None:
        LOGGER.error("Unhandled UI callback error", exc_info=(exception_type, exception, traceback))
        messagebox.showerror(
            "Operation failed",
            f"The operation could not be completed:\n{exception}\n\nDiagnostic details were written to the log.",
            parent=self.root,
        )

    def _open_data_management(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Data Management")
        window.transient(self.root)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        location = self.character_store.path if not self.storage_error else app_data_dir() / "characters.db"
        ttk.Label(body, text=f"Application data: {location.parent}", wraplength=540).pack(anchor="w")
        ttk.Label(body, text="Dataset share link:").pack(anchor="w", pady=(12, 0))
        dataset_entry = ttk.Entry(body, textvariable=self.dataset_url_var, width=76)
        dataset_entry.pack(fill="x", pady=(2, 0))
        ttk.Label(body, text="Save a public Google Sheets share link. The link stays only in this computer's settings and is not bundled with the app.", wraplength=540).pack(anchor="w", pady=(3, 0))
        ttk.Button(body, text="Save dataset", command=lambda: self._save_dataset_url(window)).pack(anchor="w", pady=(6, 0))
        if self.storage_error:
            ttk.Label(
                body,
                text="Normal storage is unavailable. Restore a backup before continuing regular work.",
                foreground="#9c1c1c",
                wraplength=540,
            ).pack(anchor="w", pady=(6, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(
            buttons,
            text="Export Backup…",
            command=lambda: self._export_data_backup(window),
            state="disabled" if self.storage_error else "normal",
        ).pack(side="left")
        ttk.Button(buttons, text="Restore Backup…", command=lambda: self._restore_data_backup(window)).pack(side="left", padx=6)
        ttk.Button(buttons, text="Open Log Folder", command=self._open_log_folder).pack(side="left")
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right")

    def _save_dataset_url(self, parent: tk.Misc) -> None:
        value = self.dataset_url_var.get().strip()
        if not value:
            messagebox.showwarning("Dataset link required", "Enter a public Google Sheets share link.", parent=parent)
            return
        try:
            client = CatalogClient(dataset_url=value)
        except CatalogError as exc:
            messagebox.showwarning("Invalid dataset link", str(exc), parent=parent)
            return
        self.client = client
        self.snapshot = None
        self._snapshot_is_cached = False
        self._refresh_catalog_view()
        try:
            self._save_user_settings()
        except OSError as exc:
            messagebox.showwarning("Dataset not saved", str(exc), parent=parent)
            return
        self.sync_status_var.set("Catalog: dataset saved — refreshing")
        self._request_refresh()

    def _export_data_backup(self, parent: tk.Misc) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = filedialog.asksaveasfilename(
            parent=parent,
            title="Export application backup",
            defaultextension=".zip",
            initialfile=f"CTP500-backup-{stamp}.zip",
            filetypes=(("ZIP backup", "*.zip"),),
        )
        if not filename:
            return
        try:
            source = self.character_store.path.parent
            path = export_backup(Path(filename), source)
            LOGGER.info("Exported data backup to %s", path)
            messagebox.showinfo("Backup complete", f"Backup saved to:\n{path}", parent=parent)
        except Exception as exc:
            LOGGER.exception("Backup export failed")
            messagebox.showerror("Backup failed", str(exc), parent=parent)

    def _restore_data_backup(self, parent: tk.Misc) -> None:
        filename = filedialog.askopenfilename(
            parent=parent,
            title="Restore application backup",
            filetypes=(("ZIP backup", "*.zip"), ("All files", "*.*")),
        )
        if not filename:
            return
        try:
            files = validate_backup(Path(filename))
        except BackupError as exc:
            LOGGER.warning("Rejected backup %s: %s", filename, exc)
            messagebox.showerror("Invalid backup", str(exc), parent=parent)
            return
        if not messagebox.askyesno(
            "Restore backup?",
            "This will replace the corresponding local data files and clear the current in-memory "
            f"character and encounter state.\n\nFiles: {', '.join(files)}\n\nContinue?",
            parent=parent,
        ):
            return
        try:
            safety = restore_backup(Path(filename))
            self.character_store = CharacterStore()
            self.encounter_store = EncounterStore(self.character_store.path)
            self.merchant_store = MerchantStore(self.character_store.path)
            self.current_character = None
            self.current_encounter = None
            self.encounter_controller = None
            self.storage_error = ""
            self._restored_settings_pending_restart = True
            self.snapshot = load_catalog_cache()
            self._snapshot_is_cached = self.snapshot is not None
            if self.snapshot:
                self._update_filter_values()
                self._refresh_catalog_view()
                self.sync_status_var.set(self._cached_catalog_status())
            else:
                self._update_filter_values()
                self._refresh_catalog_view()
                self.sync_status_var.set("Catalog: unavailable — waiting for refresh")
            self._refresh_saved_characters()
            self._refresh_encounter_library()
            LOGGER.info("Restored data backup from %s", filename)
            note = f"\n\nPre-restore safety backup:\n{safety}" if safety else ""
            messagebox.showinfo(
                "Restore complete",
                f"Application data was restored.{note}\n\nClose and reopen the app to apply restored preferences. "
                "They will not be overwritten when this session closes.",
                parent=parent,
            )
            parent.destroy()
        except Exception as exc:
            LOGGER.exception("Backup restore failed")
            messagebox.showerror("Restore failed", str(exc), parent=parent)

    def _open_log_folder(self) -> None:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(directory)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("Logs", f"Could not open the log folder:\n{exc}\n\n{directory}", parent=self.root)

    def _close_race_editor(self, window: tk.Toplevel) -> None:
        enabled = [race.name for race in RACES if self.parent_enabled.get(race_parent(race.name), True) and self.parent_weights.get(race_parent(race.name), 1) > 0 and self.race_enabled.get(race.name, True) and self.race_weights.get(race.name, 1) > 0]
        self.race_box.configure(values=("Random", *enabled))
        if self.char_race_var.get() not in {"Random", *enabled}:
            self.char_race_var.set("Random")
        window.destroy()

    def _start_sync(self) -> None:
        threading.Thread(target=self._sync_loop, name="gear-catalog-sync", daemon=True).start()

    def _sync_loop(self) -> None:
        while not self._sync_stop.is_set():
            self.events.put(("syncing", None))
            try:
                snapshot = self.client.fetch()
                try:
                    save_catalog_cache(snapshot)
                except OSError:
                    LOGGER.exception("Could not save catalog cache")
                LOGGER.info("Catalog refresh succeeded with %d items", len(snapshot.items))
                self.events.put(("snapshot", snapshot))
            except Exception as exc:
                LOGGER.warning("Catalog refresh failed: %s", exc)
                self.events.put(("sync_error", str(exc)))
            self._sync_trigger.wait(SYNC_INTERVAL_SECONDS)
            self._sync_trigger.clear()

    def _request_refresh(self) -> None:
        self._sync_trigger.set()

    def _check_for_updates(self, automatic: bool = False) -> None:
        if hasattr(self, "update_button"):
            self.update_button.configure(state="disabled")

        def check() -> None:
            try:
                self.events.put(("update_result", (fetch_latest_release(), automatic)))
            except Exception as exc:
                LOGGER.info("Update check failed: %s", exc)
                self.events.put(("update_error", (str(exc), automatic)))

        threading.Thread(target=check, name="github-update-check", daemon=True).start()

    def _offer_update(self, release: ReleaseInfo | None, automatic: bool) -> None:
        if hasattr(self, "update_button"):
            self.update_button.configure(state="normal")
        if not release or not is_newer(release, __version__):
            if not automatic:
                messagebox.showinfo("Updates", "You are running the latest available release.", parent=self.root)
            return
        notes = release.notes[:1200] or "No release notes were provided."
        if messagebox.askyesno(
            "Update available",
            f"Version {release.version} is available (you have {__version__}).\n\n{notes}\n\nOpen the official release page?",
            parent=self.root,
        ):
            webbrowser.open(RELEASES_PAGE_URL)

    def _drain_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "syncing":
                    if self.snapshot:
                        self.sync_status_var.set(self._cached_catalog_status("Catalog: refreshing; last good copy"))
                    else:
                        self.sync_status_var.set("Catalog: refreshing…")
                elif event == "snapshot":
                    self._accept_snapshot(payload)  # type: ignore[arg-type]
                elif event == "sync_error":
                    if self.snapshot:
                        self.sync_status_var.set(self._cached_catalog_status("Catalog: offline; cached copy"))
                    else:
                        self.sync_status_var.set(f"Catalog: unavailable — {payload}")
                elif event == "jobs":
                    self._refresh_jobs()
                elif event == "printer_connected":
                    self._printer_connected(payload)  # type: ignore[arg-type]
                elif event == "printer_error":
                    self._printer_error(str(payload))
                elif event == "printer_disconnected":
                    self._printer_disconnected()
                elif event == "update_result":
                    release, automatic = payload  # type: ignore[misc]
                    self._offer_update(release, automatic)
                elif event == "update_error":
                    error, automatic = payload  # type: ignore[misc]
                    if hasattr(self, "update_button"):
                        self.update_button.configure(state="normal")
                    if not automatic:
                        messagebox.showwarning("Update check failed", error, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _accept_snapshot(self, snapshot: CatalogSnapshot) -> None:
        changed = self.snapshot is None or self.snapshot.digest != snapshot.digest
        self.snapshot = snapshot
        self._snapshot_is_cached = False
        timestamp = snapshot.fetched_at.strftime("%H:%M:%S")
        self.sync_status_var.set(f"Catalog: live — updated {timestamp}")
        if changed:
            self._update_filter_values()
            self._refresh_catalog_view()

    def _update_filter_values(self) -> None:
        items = self.snapshot.items if self.snapshot else ()
        options = {
            "Category": [name for name, _gid in CATEGORY_GIDS],
            "Manufacturer": manufacturer_options(items),
            "Kind": sorted({item.kind for item in items if item.kind}, key=natural_key),
            "Rank": sorted({item.rank for item in items if item.rank}, key=natural_key),
        }
        variables = {
            "Category": self.category_var,
            "Manufacturer": self.manufacturer_var,
            "Kind": self.kind_var,
            "Rank": self.rank_var,
        }
        for label, values in options.items():
            self.filter_boxes[label]["values"] = ("All", *values)
            if variables[label].get() not in {"All", *values}:
                variables[label].set("All")

    def _refresh_catalog_view(self) -> None:
        selected_identities = {
            self.item_by_iid[iid].identity
            for iid in self.catalog_tree.selection()
            if iid in self.item_by_iid
        }
        snapshot_items = self.snapshot.items if self.snapshot else ()
        category = self.category_var.get()
        columns = catalog_columns(snapshot_items, category)
        if tuple(self.catalog_tree["columns"]) != columns:
            self._configure_catalog_columns(columns, category)
        if self.sort_column not in columns:
            self.sort_column = "Name" if "Name" in columns else columns[0]
            self.sort_reverse = False

        items = filter_items(
            snapshot_items,
            query=self.search_var.get(),
            category=category,
            manufacturer=self.manufacturer_var.get(),
            kind=self.kind_var.get(),
            rank=self.rank_var.get(),
        )
        items.sort(
            key=lambda item: natural_key(catalog_value(item, self.sort_column, category)),
            reverse=self.sort_reverse,
        )
        self.visible_items = items
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        self.item_by_iid.clear()
        restore: list[str] = []
        for index, item in enumerate(items):
            iid = f"gear-{index}"
            self.item_by_iid[iid] = item
            self.catalog_tree.insert(
                "",
                "end",
                iid=iid,
                values=tuple(catalog_value(item, column, category) for column in columns),
            )
            if item.identity in selected_identities:
                restore.append(iid)
        if restore:
            self.catalog_tree.selection_set(restore)
        total = len(self.snapshot.items) if self.snapshot else 0
        self.item_count_var.set(f"{len(items):,} shown of {total:,} items")

    def _configure_catalog_columns(self, columns: tuple[str, ...], view: str) -> None:
        self.catalog_tree.configure(columns=columns)
        saved_widths = self.column_widths.get(view, {})
        for column in columns:
            self.catalog_tree.heading(column, text=column, command=lambda col=column: self._sort_by(col))
            default_width = GENERAL_COLUMN_WIDTHS.get(column, max(85, min(220, len(column) * 10)))
            self.catalog_tree.column(
                column,
                width=saved_widths.get(column, default_width),
                minwidth=55,
                stretch=stretch_catalog_column(column, columns),
            )

    def _column_resize_finished(self, event) -> None:
        if self.catalog_tree.identify_region(event.x, event.y) != "separator":
            return
        if not self._restored_settings_pending_restart:
            self._remember_column_widths()
            try:
                self._save_user_settings()
            except OSError:
                LOGGER.exception("Could not save settings during shutdown")

    def _remember_column_widths(self) -> None:
        columns = tuple(self.catalog_tree["columns"])
        self.column_widths[self.category_var.get()] = {
            column: int(self.catalog_tree.column(column, "width")) for column in columns
        }

    def _save_user_settings(self) -> None:
        character_settings = dict(self.saved_character_settings)
        try:
            current = self._character_settings()
            if current:
                character_settings = asdict(current)
        except Exception:
            pass
        save_user_settings(
            UserSettings(
                bluetooth_address=self.address_var.get().strip(),
                column_widths=self.column_widths,
                character_settings=character_settings,
                encounter_generation_settings=self.saved_encounter_generation_settings,
                dataset_url=self.dataset_url_var.get().strip(),
                initiative_presets=tuple(self.initiative_presets),
            )
        )

    def _sort_by(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self._refresh_catalog_view()

    def _clear_filters(self) -> None:
        self.search_var.set("")
        self.category_var.set("All")
        self.manufacturer_var.set("All")
        self.kind_var.set("All")
        self.rank_var.set("All")
        self._refresh_catalog_view()

    def _selected_items(self) -> list[GearItem]:
        return [self.item_by_iid[iid] for iid in self.catalog_tree.selection() if iid in self.item_by_iid]

    def _show_details(self, _event=None) -> None:
        selected = self._selected_items()
        text = ""
        if selected:
            item = selected[0]
            source = "\n".join(
                f"{label}: {value}"
                for label, value in item.fields
                if value.strip().casefold() not in {"", "-", "none"}
            )
            text = f"SOURCE ROW ({item.category}, row {item.source_row})\n{source}\n\nPRINTED TAG\n{tag_text(item)}"
            if len(selected) > 1:
                text += f"\n\n{len(selected)} items selected."
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", "end")
        self.details_text.insert("1.0", text)
        self.details_text.configure(state="disabled")

    def _valid_quantity(self) -> int | None:
        try:
            copies = int(self.quantity_var.get())
            if not 1 <= copies <= 99:
                raise ValueError
            return copies
        except (ValueError, tk.TclError):
            messagebox.showwarning("Invalid quantity", "Copies must be between 1 and 99.")
            return None

    def _queue_selected(self) -> None:
        items = self._selected_items()
        if not items:
            messagebox.showwarning("Nothing selected", "Select one or more gear items first.")
            return
        copies = self._valid_quantity()
        if copies is not None:
            self.spooler.enqueue(items, copies)

    def _quick_print(self, event) -> None:
        iid = self.catalog_tree.identify_row(event.y)
        if iid and iid in self.item_by_iid:
            self.catalog_tree.selection_set(iid)
            self.spooler.enqueue([self.item_by_iid[iid]], 1, priority=True)

    def _refresh_jobs(self) -> None:
        selected_job = self._selected_job_id()
        selected_char_job = self._selected_char_job_id() if hasattr(self, "char_spool_tree") else None
        jobs = self.spooler.jobs()
        self._populate_spool_tree(self.spool_tree, self.job_by_iid, jobs, selected_job)
        if hasattr(self, "char_spool_tree"):
            self._populate_spool_tree(self.char_spool_tree, self.char_job_by_iid, jobs, selected_char_job)
        if not self.printer.connected and any(job.status == JobStatus.FAILED for job in jobs):
            self.printer_status_var.set("Printer: disconnected after print failure — queue paused")
        self._show_job_error()
        self._show_char_job_error()

    @staticmethod
    def _populate_spool_tree(tree: ttk.Treeview, mapping: dict[str, str], jobs, selected_job: str | None) -> None:
        tree.delete(*tree.get_children())
        mapping.clear()
        restore = None
        for index, job in enumerate(jobs):
            iid = f"job-{index}"
            mapping[iid] = job.id
            copies = f"{job.total_copies - job.remaining_copies}/{job.total_copies}"
            if job.status == JobStatus.CANCELED:
                copies = f"—/{job.total_copies}"
            tree.insert("", "end", iid=iid, values=(job.item.name, job.item.category, copies, job.status.value))
            if job.id == selected_job:
                restore = iid
        if restore:
            tree.selection_set(restore)

    def _selected_job_id(self) -> str | None:
        selection = self.spool_tree.selection()
        return self.job_by_iid.get(selection[0]) if selection else None

    def _selected_job(self):
        job_id = self._selected_job_id()
        return next((job for job in self.spooler.jobs() if job.id == job_id), None)

    def _show_job_error(self, _event=None) -> None:
        job = self._selected_job()
        self.job_error_var.set(job.error if job else "")

    def _selected_char_job_id(self) -> str | None:
        if not hasattr(self, "char_spool_tree"):
            return None
        selection = self.char_spool_tree.selection()
        return self.char_job_by_iid.get(selection[0]) if selection else None

    def _show_char_job_error(self, _event=None) -> None:
        if not hasattr(self, "char_job_error_var"):
            return
        job_id = self._selected_char_job_id()
        job = next((job for job in self.spooler.jobs() if job.id == job_id), None)
        self.char_job_error_var.set(job.error if job else "")

    def _char_job_action(self, action: str) -> None:
        job_id = self._selected_char_job_id()
        if not job_id:
            return
        if action == "cancel":
            self.spooler.cancel(job_id)
        elif action == "retry":
            self.spooler.retry(job_id)
        elif action == "remove":
            self.spooler.remove(job_id)

    def _cancel_job(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.spooler.cancel(job_id)

    def _retry_job(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.spooler.retry(job_id)

    def _remove_job(self) -> None:
        job_id = self._selected_job_id()
        if job_id:
            self.spooler.remove(job_id)

    def _set_connection_busy(self, busy: bool) -> None:
        self.connect_button.configure(state="disabled" if busy else "normal")
        self.disconnect_button.configure(state="disabled" if busy else "normal")
        self.address_entry.configure(state="disabled" if busy else "normal")

    def _connect(self) -> None:
        try:
            address = normalize_address(self.address_var.get())
        except ValueError as exc:
            messagebox.showwarning("Invalid Bluetooth address", str(exc))
            return
        self._set_connection_busy(True)
        self.printer_status_var.set("Printer: connecting…")

        def work() -> None:
            try:
                status = self.printer.connect(address)
                self.events.put(("printer_connected", (address, status)))
            except Exception as exc:
                self.events.put(("printer_error", str(exc)))

        threading.Thread(target=work, name="ctp500-connect", daemon=True).start()

    def _disconnect(self) -> None:
        self._set_connection_busy(True)
        self.printer_status_var.set("Printer: disconnecting…")

        def work() -> None:
            self.printer.disconnect()
            self.events.put(("printer_disconnected", None))

        threading.Thread(target=work, name="ctp500-disconnect", daemon=True).start()

    def _printer_connected(self, payload: tuple[str, bytes]) -> None:
        address, status = payload
        self.address_var.set(address)
        try:
            self._save_user_settings()
        except OSError as exc:
            messagebox.showwarning("Settings", f"Connected, but could not save the address: {exc}")
        status_note = f", status {status.hex(' ')}" if status else ""
        self.printer_status_var.set(f"Printer: connected to {address}{status_note}")
        self._set_connection_busy(False)
        self.spooler.wake()

    def _printer_error(self, error: str) -> None:
        self.printer_status_var.set(f"Printer: {error}")
        self._set_connection_busy(False)

    def _printer_disconnected(self) -> None:
        self.printer_status_var.set("Printer: disconnected — queue paused")
        self._set_connection_busy(False)

    def _on_close(self) -> None:
        if self.spooler.has_unfinished_jobs() and not messagebox.askyesno(
            "Discard print queue?",
            "Print jobs are pending or active. Close the app and discard the session queue?",
        ):
            return
        self._remember_column_widths()
        try:
            self._save_user_settings()
        except OSError:
            pass
        self._closing = True
        self._sync_stop.set()
        self._sync_trigger.set()
        self.printer.close()
        stopped = self.spooler.stop(abort=True)
        if not stopped:
            LOGGER.warning("Print spooler did not stop before shutdown timeout")
        if self._temporary_storage is not None:
            self._temporary_storage.cleanup()
        self.root.destroy()


def run() -> None:
    root = tk.Tk()
    GearPrinterApp(root)
    root.mainloop()
