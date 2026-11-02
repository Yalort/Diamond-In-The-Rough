from __future__ import annotations

import csv
import hashlib
import io
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Iterable

from .config import CATEGORY_GIDS, HTTP_TIMEOUT_SECONDS
from .models import CatalogSnapshot, GearItem


MAX_CATEGORY_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_CATEGORY_ROWS = 10_000
MAX_CATALOG_COLUMNS = 128
MAX_CATALOG_FIELD_CHARS = 8_192
MAX_CATALOG_ROW_CHARS = 32_768
MAX_CATALOG_ITEMS = 50_000
RESPONSE_CHUNK_BYTES = 64 * 1024


class CatalogError(RuntimeError):
    pass


def spreadsheet_id_from_share_link(value: str) -> str:
    """Extract a Google Sheets ID from a saved public share link or raw ID."""
    source = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", source):
        return source
    parsed = urllib.parse.urlparse(source)
    host = parsed.netloc.casefold()
    match = re.fullmatch(r"/spreadsheets/d/([A-Za-z0-9_-]+)(?:/.*)?", parsed.path)
    if host in {"docs.google.com", "www.docs.google.com"} and match:
        return match.group(1)
    if host in {"drive.google.com", "www.drive.google.com"} and parsed.path in {"/open", "/uc"}:
        candidate = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{20,}", candidate):
            return candidate
    raise CatalogError("Enter a public Google Sheets share link (or spreadsheet ID).")


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _validate_text(value: object, label: str) -> str:
    text = _clean(value)
    if len(text) > MAX_CATALOG_FIELD_CHARS:
        raise CatalogError(f"{label} exceeds the allowed length")
    return text


def validate_catalog_items(items: Iterable[GearItem]) -> tuple[GearItem, ...]:
    validated: list[GearItem] = []
    for item in items:
        if len(validated) >= MAX_CATALOG_ITEMS:
            raise CatalogError("Catalog contains too many items")
        if len(item.fields) > MAX_CATALOG_COLUMNS:
            raise CatalogError("Catalog item contains too many columns")
        row_chars = 0
        for key, value in item.fields:
            key = _validate_text(key, "Catalog field name")
            value = _validate_text(value, "Catalog field value")
            row_chars += len(key) + len(value)
        for label, value in (
            ("Catalog category", item.category),
            ("Catalog name", item.name),
            ("Catalog manufacturer", item.manufacturer),
            ("Catalog kind", item.kind),
            ("Catalog rank", item.rank),
            ("Catalog price", item.price),
        ):
            _validate_text(value, label)
        if row_chars > MAX_CATALOG_ROW_CHARS:
            raise CatalogError("Catalog item exceeds the allowed row size")
        validated.append(item)
    return tuple(validated)


def _kind_for(category: str, fields: dict[str, str]) -> str:
    if category == "Weapons":
        return fields.get("Form Factor", "")
    if category == "Apparel":
        return fields.get("Coverage", "")
    if category == "Gene Treatments":
        return "Gene Treatment"
    return fields.get("Type", "")


def parse_category_csv(category: str, gid: int, payload: bytes) -> tuple[GearItem, ...]:
    if len(payload) > MAX_CATEGORY_RESPONSE_BYTES:
        raise CatalogError(f"{category} exceeds the allowed download size")
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    headers = [_validate_text(header, f"{category} column name") for header in (reader.fieldnames or [])]
    if not headers:
        # Google returns an empty body for a completely empty, preformatted tab.
        return ()
    if "Name" not in headers:
        raise CatalogError(f"{category} is missing the required Name column")
    if len(headers) > MAX_CATALOG_COLUMNS:
        raise CatalogError(f"{category} contains too many columns")

    items: list[GearItem] = []
    for source_row, raw_row in enumerate(reader, start=2):
        if source_row - 1 > MAX_CATEGORY_ROWS:
            raise CatalogError(f"{category} contains too many rows")
        row_chars = 0
        for key, value in raw_row.items():
            values = value if isinstance(value, list) else [value]
            for part in values:
                row_chars += len(_validate_text(part, f"{category} field value"))
            if key is not None:
                row_chars += len(_validate_text(key, f"{category} column name"))
        if row_chars > MAX_CATALOG_ROW_CHARS:
            raise CatalogError(f"{category} row {source_row} exceeds the allowed size")
        cleaned = {
            _clean(key): _clean(value)
            for key, value in raw_row.items()
            if key is not None and _clean(key)
        }
        name = cleaned.get("Name", "")
        if not name:
            continue
        ordered_fields = tuple((header, cleaned.get(header, "")) for header in headers)
        items.append(
            GearItem(
                category=category,
                source_gid=gid,
                source_row=source_row,
                fields=ordered_fields,
                name=name,
                manufacturer=cleaned.get("Manufacturer", ""),
                kind=_kind_for(category, cleaned),
                rank=cleaned.get("Rank", ""),
                price=cleaned.get("Price", ""),
            )
        )
    return validate_catalog_items(items)


def _read_response(response, category: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := response.read(min(RESPONSE_CHUNK_BYTES, MAX_CATEGORY_RESPONSE_BYTES - total + 1)):
        total += len(chunk)
        if total > MAX_CATEGORY_RESPONSE_BYTES:
            raise CatalogError(f"{category} exceeds the allowed download size")
        chunks.append(chunk)
    return b"".join(chunks)


class CatalogClient:
    def __init__(
        self,
        spreadsheet_id: str = "",
        dataset_url: str = "",
        categories: tuple[tuple[str, int], ...] = CATEGORY_GIDS,
        timeout: int = HTTP_TIMEOUT_SECONDS,
        opener=urllib.request.urlopen,
    ) -> None:
        self.dataset_url = dataset_url.strip() or spreadsheet_id.strip()
        self.spreadsheet_id = spreadsheet_id_from_share_link(self.dataset_url) if self.dataset_url else ""
        self.categories = categories
        self.timeout = timeout
        self._opener = opener

    def _url(self, gid: int) -> str:
        query = urllib.parse.urlencode(
            {"format": "csv", "gid": gid, "_": int(time.time())}
        )
        return (
            f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/export?{query}"
        )

    def fetch(self) -> CatalogSnapshot:
        if not self.spreadsheet_id:
            raise CatalogError("No dataset is configured. Save a public Google Sheets share link in Data Management.")
        all_items: list[GearItem] = []
        digest = hashlib.sha256()
        try:
            for category, gid in self.categories:
                request = urllib.request.Request(
                    self._url(gid),
                    headers={"User-Agent": "CTP500GearPrinter/1.0"},
                )
                with self._opener(request, timeout=self.timeout) as response:
                    payload = _read_response(response, category)
                digest.update(str(gid).encode("ascii"))
                digest.update(payload)
                all_items.extend(parse_category_csv(category, gid, payload))
                if len(all_items) > MAX_CATALOG_ITEMS:
                    raise CatalogError("Catalog contains too many items")
        except Exception as exc:
            if isinstance(exc, CatalogError):
                raise
            raise CatalogError(f"Could not refresh the gear catalog: {exc}") from exc
        return CatalogSnapshot(
            items=validate_catalog_items(all_items),
            fetched_at=datetime.now().astimezone(),
            digest=digest.hexdigest(),
        )


_NATURAL_PART = re.compile(r"(\d+(?:\.\d+)?)")


def natural_key(value: str) -> tuple[tuple[int, object], ...]:
    parts = _NATURAL_PART.split(value.casefold())
    result: list[tuple[int, object]] = []
    for part in parts:
        if not part:
            continue
        try:
            result.append((0, float(part)))
        except ValueError:
            result.append((1, part))
    return tuple(result)


def manufacturer_names(value: str) -> tuple[str, ...]:
    """Split a source cell into unique, ordered manufacturer names."""
    names: dict[str, str] = {}
    for part in value.split(","):
        name = part.strip()
        if name:
            names.setdefault(name.casefold(), name)
    return tuple(names.values())


def manufacturer_options(items: Iterable[GearItem]) -> list[str]:
    """Return unique filter choices from single- and multi-manufacturer rows."""
    names: dict[str, str] = {}
    for item in items:
        for name in manufacturer_names(item.manufacturer):
            if name.casefold() != "all":
                names.setdefault(name.casefold(), name)
    return sorted(names.values(), key=natural_key)


def _matches_manufacturer(item: GearItem, manufacturer: str) -> bool:
    if manufacturer == "All":
        return True
    names = {name.casefold() for name in manufacturer_names(item.manufacturer)}
    return manufacturer.casefold() in names or "all" in names


def filter_items(
    items: Iterable[GearItem],
    *,
    query: str = "",
    category: str = "All",
    manufacturer: str = "All",
    kind: str = "All",
    rank: str = "All",
) -> list[GearItem]:
    needle = query.strip().casefold()
    return [
        item
        for item in items
        if (not needle or needle in item.search_text)
        and (category == "All" or item.category == category)
        and _matches_manufacturer(item, manufacturer)
        and (kind == "All" or item.kind == kind)
        and (rank == "All" or item.rank == rank)
    ]
