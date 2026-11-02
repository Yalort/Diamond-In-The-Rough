from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


CATEGORY_GIDS: tuple[tuple[str, int], ...] = (
    ("Weapons", 0),
    ("Gene Treatments", 2046699994),
    ("Apparel", 926948034),
    ("Consumables/Gear", 774912175),
    ("Bionics", 1504498356),
    ("Vehicles", 446697712),
)
EXCLUDED_CATEGORIES = frozenset({"Packing Sheet"})

SYNC_INTERVAL_SECONDS = 15
HTTP_TIMEOUT_SECONDS = 20
PRINTER_WIDTH = 384
PRINTER_CONNECT_TIMEOUT_SECONDS = 10
PRINTER_IO_TIMEOUT_SECONDS = 15
RASTER_BAND_HEIGHT = 128
RASTER_BAND_PAUSE_SECONDS = 0.02


def app_data_dir() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / ".config"))
    return base / "CTP500GearPrinter"


def _config_path() -> Path:
    return app_data_dir() / "config.json"


@dataclass(frozen=True, slots=True)
class UserSettings:
    bluetooth_address: str = ""
    column_widths: dict[str, dict[str, int]] = field(default_factory=dict)
    character_settings: dict[str, object] = field(default_factory=dict)
    encounter_generation_settings: dict[str, int] = field(default_factory=dict)
    dataset_url: str = ""
    initiative_presets: tuple[tuple[str, int], ...] = ()


def _load_column_widths(value: object) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, int]] = {}
    for view, widths in value.items():
        if not isinstance(view, str) or not isinstance(widths, dict):
            continue
        valid = {
            column: width
            for column, width in widths.items()
            if isinstance(column, str)
            and isinstance(width, int)
            and not isinstance(width, bool)
            and 55 <= width <= 2000
        }
        if valid:
            result[view] = valid
    return result


def _load_initiative_presets(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        name, initiative = entry
        if not isinstance(name, str) or not isinstance(initiative, int) or isinstance(initiative, bool):
            continue
        preset = (name.strip()[:80], max(-100, min(200, initiative)))
        if not preset[0] or preset in seen:
            continue
        result.append(preset)
        seen.add(preset)
        if len(result) == 8:
            break
    return tuple(result)


def load_user_settings() -> UserSettings:
    path = _config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return UserSettings(
            bluetooth_address=str(data.get("bluetooth_address", "")),
            column_widths=_load_column_widths(data.get("column_widths")),
            character_settings=data.get("character_settings", {}) if isinstance(data.get("character_settings"), dict) else {},
            encounter_generation_settings={
                key: value for key, value in data.get("encounter_generation_settings", {}).items()
                if key in {"party_size", "party_power_level"} and isinstance(value, int) and not isinstance(value, bool)
            } if isinstance(data.get("encounter_generation_settings"), dict) else {},
            dataset_url=str(data.get("dataset_url", "")).strip()[:2000],
            initiative_presets=_load_initiative_presets(data.get("initiative_presets")),
        )
    except (OSError, ValueError, TypeError):
        return UserSettings()


def save_user_settings(settings: UserSettings) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "bluetooth_address": settings.bluetooth_address,
            "column_widths": settings.column_widths,
            "character_settings": settings.character_settings,
            "encounter_generation_settings": settings.encounter_generation_settings,
            "dataset_url": settings.dataset_url,
            "initiative_presets": settings.initiative_presets,
        },
        indent=2,
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
