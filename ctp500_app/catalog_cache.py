from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .config import app_data_dir
from .catalog import MAX_CATALOG_COLUMNS, MAX_CATALOG_ITEMS, validate_catalog_items
from .models import CatalogSnapshot, GearItem


CACHE_VERSION = 1
MAX_CATALOG_CACHE_BYTES = 96 * 1024 * 1024
LOGGER = logging.getLogger("ctp500.catalog_cache")


def catalog_cache_path() -> Path:
    return app_data_dir() / "catalog_cache.json"


def _item_to_dict(item: GearItem) -> dict[str, object]:
    return {
        "category": item.category,
        "source_gid": item.source_gid,
        "source_row": item.source_row,
        "fields": [list(field) for field in item.fields],
        "name": item.name,
        "manufacturer": item.manufacturer,
        "kind": item.kind,
        "rank": item.rank,
        "price": item.price,
    }


def _item_from_dict(value: object) -> GearItem:
    if not isinstance(value, dict):
        raise ValueError("Catalog item is not an object")
    fields_value = value.get("fields")
    if not isinstance(fields_value, list):
        raise ValueError("Catalog item fields are invalid")
    if len(fields_value) > MAX_CATALOG_COLUMNS:
        raise ValueError("Catalog item has too many fields")
    fields: list[tuple[str, str]] = []
    for field in fields_value:
        if not isinstance(field, list) or len(field) != 2 or not all(isinstance(part, str) for part in field):
            raise ValueError("Catalog item field is invalid")
        fields.append((field[0], field[1]))
    string_keys = ("category", "name", "manufacturer", "kind", "rank", "price")
    if any(not isinstance(value.get(key), str) for key in string_keys):
        raise ValueError("Catalog item text is invalid")
    source_gid = value.get("source_gid")
    source_row = value.get("source_row")
    if not isinstance(source_gid, int) or isinstance(source_gid, bool):
        raise ValueError("Catalog source gid is invalid")
    if not isinstance(source_row, int) or isinstance(source_row, bool) or source_row < 1:
        raise ValueError("Catalog source row is invalid")
    return GearItem(
        category=value["category"], source_gid=source_gid, source_row=source_row,
        fields=tuple(fields), name=value["name"], manufacturer=value["manufacturer"],
        kind=value["kind"], rank=value["rank"], price=value["price"],
    )


def snapshot_to_dict(snapshot: CatalogSnapshot) -> dict[str, object]:
    return {
        "version": CACHE_VERSION,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "digest": snapshot.digest,
        "items": [_item_to_dict(item) for item in snapshot.items],
    }


def snapshot_from_dict(value: object) -> CatalogSnapshot:
    if not isinstance(value, dict) or value.get("version") != CACHE_VERSION:
        raise ValueError("Unsupported catalog cache version")
    fetched_text, digest, items_value = value.get("fetched_at"), value.get("digest"), value.get("items")
    if not isinstance(fetched_text, str) or not isinstance(digest, str) or not isinstance(items_value, list):
        raise ValueError("Catalog cache is incomplete")
    if len(items_value) > MAX_CATALOG_ITEMS:
        raise ValueError("Catalog cache contains too many items")
    fetched_at = datetime.fromisoformat(fetched_text)
    if fetched_at.tzinfo is None:
        raise ValueError("Catalog cache timestamp has no timezone")
    return CatalogSnapshot(
        items=validate_catalog_items(_item_from_dict(item) for item in items_value),
        fetched_at=fetched_at,
        digest=digest,
    )


def load_catalog_cache(path: Path | None = None) -> CatalogSnapshot | None:
    target = path or catalog_cache_path()
    try:
        with target.open("rb") as stream:
            payload = stream.read(MAX_CATALOG_CACHE_BYTES + 1)
        if len(payload) > MAX_CATALOG_CACHE_BYTES:
            raise ValueError("Catalog cache exceeds the allowed size")
        return snapshot_from_dict(json.loads(payload.decode("utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        LOGGER.warning("Ignoring invalid catalog cache %s: %s", target, exc)
        return None


def save_catalog_cache(snapshot: CatalogSnapshot, path: Path | None = None) -> None:
    target = path or catalog_cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    validate_catalog_items(snapshot.items)
    payload = json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_CATALOG_CACHE_BYTES:
        raise ValueError("Catalog cache exceeds the allowed size")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
