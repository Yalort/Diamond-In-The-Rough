"""Catalog-backed merchant generation, pricing, and transaction documents."""
from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from uuid import uuid4

from .models import GearItem, PrintDocument


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def number(value: str) -> float:
    """Extract a catalog number without making malformed prices fatal."""
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group()) if match else 0.0


@dataclass(slots=True)
class MerchantTag:
    mode: str = "Favor"  # Guarantee, Favor, Avoid, Forbid
    facet: str = "Text"  # Category, Manufacturer, Kind, Rank, Price Band, Text, Label
    value: str = ""
    values: list[str] = field(default_factory=list)
    label: str = ""

    def selected_values(self) -> list[str]:
        """New records use values; value keeps pre-1.1.1 records readable."""
        return [entry for entry in self.values if entry.strip()] or ([self.value] if self.value.strip() else [])

    def matches(self, item: GearItem) -> bool:
        values = [entry.strip().casefold() for entry in self.selected_values() if entry.strip()]
        if not values:
            return False
        fields = {"category": item.category, "manufacturer": item.manufacturer, "kind": item.kind, "rank": item.rank}
        facet = self.facet.casefold()
        if facet == "price band":
            price = number(item.price)
            bands = {"free": price == 0, "cheap": 0 < price <= 100, "working": 100 < price <= 1000,
                     "professional": 1000 < price <= 10000, "affluent": 10000 < price <= 100000, "elite": price > 100000}
            return any(bands.get(value, False) for value in values)
        if facet == "label":
            labels = {part.strip().casefold() for part in (item.category, item.manufacturer, item.kind) if part}
            return any(value in labels for value in values)
        if facet == "text":
            return any(value in item.search_text for value in values)
        field = fields.get(facet, "").casefold()
        return any(value in field for value in values)


@dataclass(slots=True)
class PriceRule:
    layer: str = "Global"  # Global, Affiliation, Specialty, Tag, Category, Item
    match: str = ""
    adjustment_type: str = "Percent"  # Percent or Fixed
    amount: float = 0.0
    enabled: bool = True


@dataclass(slots=True)
class HaggleRule:
    base_dc: int = 15
    success_discount: float = 0.10
    plus_five_discount: float = 0.20
    plus_ten_discount: float = 0.30


@dataclass(slots=True)
class MerchantStockLine:
    id: str = field(default_factory=lambda: str(uuid4()))
    category: str = ""
    name: str = ""
    manufacturer: str = ""
    kind: str = ""
    rank: str = ""
    base_price: float = 0.0
    snapshot: dict[str, object] = field(default_factory=dict)
    source_gid: int = 0
    source_row: int = 0
    quantity: int = 1
    locked: bool = False
    sold_out: bool = False
    item_override: PriceRule | None = None

    @classmethod
    def from_item(cls, item: GearItem, quantity: int = 1) -> "MerchantStockLine":
        return cls(category=item.category, name=item.name, manufacturer=item.manufacturer, kind=item.kind, rank=item.rank,
                   base_price=number(item.price), snapshot={"fields": list(item.fields), "price": item.price},
                   source_gid=item.source_gid, source_row=item.source_row, quantity=max(1, quantity))


@dataclass(slots=True)
class MerchantTransaction:
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=now_iso)
    direction: str = "Sale"  # Sale or Buyback
    stock_line_id: str = ""
    item_name: str = ""
    quantity: int = 1
    unit_price: float = 0.0
    final_price: float = 0.0
    actor: str = ""
    note: str = ""
    roll: int | None = None
    dc: int | None = None
    outcome: str = ""
    reversal_of: str = ""


@dataclass(slots=True)
class MerchantRecord:
    id: str = field(default_factory=lambda: str(uuid4()))
    record_type: str = "Merchant"  # Merchant or Bundle
    name: str = "New Merchant"
    wealth_tier: int = 2
    specialties: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    notes: str = ""
    cash: float = 0.0
    seed: str = ""
    catalog_digest: str = ""
    tags: list[MerchantTag] = field(default_factory=list)
    price_rules: list[PriceRule] = field(default_factory=list)
    haggle_rule: HaggleRule = field(default_factory=HaggleRule)
    stock: list[MerchantStockLine] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "MerchantRecord":
        data = dict(value)
        data["tags"] = [MerchantTag(**item) for item in data.get("tags", [])]  # type: ignore[arg-type]
        data["price_rules"] = [PriceRule(**item) for item in data.get("price_rules", [])]  # type: ignore[arg-type]
        data["haggle_rule"] = HaggleRule(**data.get("haggle_rule", {}))  # type: ignore[arg-type]
        stock = []
        for item in data.get("stock", []):  # type: ignore[assignment]
            line = dict(item)
            if line.get("item_override"):
                line["item_override"] = PriceRule(**line["item_override"])
            stock.append(MerchantStockLine(**line))
        data["stock"] = stock
        return cls(**data)  # type: ignore[arg-type]


StockBundle = MerchantRecord
Merchant = MerchantRecord


def bundle_as_merchant(bundle: StockBundle, name: str = "") -> Merchant:
    """Copy a reusable bundle; subsequent sales and edits never mutate its source."""
    clone = MerchantRecord.from_dict(bundle.to_dict())
    clone.id = str(uuid4())
    clone.record_type = "Merchant"
    clone.name = name or f"{bundle.name} shop"
    clone.created_at = clone.updated_at = now_iso()
    return clone


def reconciliation_lines(record: MerchantRecord, items: tuple[GearItem, ...], digest: str) -> list[MerchantStockLine]:
    if record.catalog_digest == digest:
        return []
    by_identity = {(item.source_gid, item.source_row): item for item in items}
    affected = []
    for line in record.stock:
        current = by_identity.get((line.source_gid, line.source_row))
        if not current or current.name != line.name or number(current.price) != line.base_price:
            affected.append(line)
    return affected


def eligible_items(items: tuple[GearItem, ...], tags: list[MerchantTag]) -> list[GearItem]:
    hard_yes = [tag for tag in tags if tag.mode == "Guarantee"]
    hard_no = [tag for tag in tags if tag.mode == "Forbid"]
    return [item for item in items if all(tag.matches(item) for tag in hard_yes) and not any(tag.matches(item) for tag in hard_no)]


STOCK_QUANTITY_RANGES = {
    "Weapons": (1, 4), "Consumables/Gear": (1, 100), "Bionics": (1, 5),
    "Gene Treatments": (1, 5), "Apparel": (1, 8), "Vehicles": (1, 2),
}


def stock_quantity(item: GearItem, rng: random.Random) -> int:
    low, high = STOCK_QUANTITY_RANGES.get(item.category, (1, 15))
    return rng.randint(low, high)


def generation_target(wealth_tier: int, candidate_count: int) -> int:
    """The fourth tier deliberately means every matching catalog row."""
    targets = {0: 25, 1: 50, 2: 100, 3: 150}
    return candidate_count if wealth_tier >= 4 else targets.get(max(0, wealth_tier), 25)


def generate_stock(record: MerchantRecord, items: tuple[GearItem, ...], digest: str, *, count: int | None = None) -> list[str]:
    """Regenerate unlocked stock. Returns human-readable unfilled reasons."""
    candidates = eligible_items(items, record.tags)
    record.stock = [line for line in record.stock if line.locked]
    if not candidates:
        record.catalog_digest = digest
        return ["No catalog items satisfy the Guarantee/Forbid tags."]
    rng = random.Random(record.seed or record.id)
    desired = count if count is not None else generation_target(record.wealth_tier, len(candidates))
    favors = [tag for tag in record.tags if tag.mode == "Favor"]
    avoids = [tag for tag in record.tags if tag.mode == "Avoid"]
    chosen: set[tuple[int, int]] = {(line.source_gid, line.source_row) for line in record.stock}
    weighted = []
    for item in candidates:
        weight = 1.0
        weight *= 5.0 ** sum(tag.matches(item) for tag in favors)
        weight *= 0.12 ** sum(tag.matches(item) for tag in avoids)
        weighted.append((item, weight))
    while len(record.stock) < desired and len(chosen) < len(candidates):
        available = [(item, weight) for item, weight in weighted if (item.source_gid, item.source_row) not in chosen]
        if not available:
            break
        item = rng.choices([item for item, _weight in available], weights=[weight for _item, weight in available], k=1)[0]
        chosen.add((item.source_gid, item.source_row))
        record.stock.append(MerchantStockLine.from_item(item, stock_quantity(item, rng)))
    record.catalog_digest = digest
    record.updated_at = now_iso()
    return [f"Only {len(candidates)} eligible unique catalog items were available; target was {desired}."] if len(record.stock) < desired else []


_ORDER = {"Global": 0, "Affiliation": 1, "Specialty": 2, "Tag": 3, "Category": 4, "Item": 5}


def price_breakdown(record: MerchantRecord, line: MerchantStockLine) -> list[tuple[str, float]]:
    price = line.base_price
    result = [("Catalog base", price)]
    rules = sorted((rule for rule in record.price_rules if rule.enabled), key=lambda rule: _ORDER.get(rule.layer, 99))
    for rule in rules:
        match = rule.match.casefold()
        applies = rule.layer == "Global" or (rule.layer == "Category" and match == line.category.casefold()) or (rule.layer == "Item" and match == line.name.casefold()) or (rule.layer == "Affiliation" and match in {x.casefold() for x in record.affiliations}) or (rule.layer == "Specialty" and match in {x.casefold() for x in record.specialties}) or (rule.layer == "Tag" and match in (line.name + " " + line.manufacturer + " " + line.kind).casefold())
        if applies:
            price = price * (1 + rule.amount / 100) if rule.adjustment_type == "Percent" else price + rule.amount
            result.append((f"{rule.layer}: {rule.amount:+g}{'%' if rule.adjustment_type == 'Percent' else ''}", max(0.0, price)))
    if line.item_override:
        rule = line.item_override
        price = price * (1 + rule.amount / 100) if rule.adjustment_type == "Percent" else rule.amount
        result.append(("Item override", max(0.0, price)))
    return result


def final_price(record: MerchantRecord, line: MerchantStockLine) -> float:
    return round(price_breakdown(record, line)[-1][1], 2)


def haggle(record: MerchantRecord, line: MerchantStockLine, roll: int) -> tuple[int, str, float]:
    dc = record.haggle_rule.base_dc + max(0, record.wealth_tier) + max(0, int(number(line.rank)) - 1)
    margin = roll - dc
    discount = record.haggle_rule.plus_ten_discount if margin >= 10 else record.haggle_rule.plus_five_discount if margin >= 5 else record.haggle_rule.success_discount if margin >= 0 else 0.0
    outcome = "Failed" if margin < 0 else f"Success ({discount:.0%})"
    return dc, outcome, round(max(0, final_price(record, line) * (1 - discount)), 2)


def merchant_document(record: MerchantRecord) -> PrintDocument:
    lines = [record.name, f"{record.record_type} | Wealth {record.wealth_tier}", ""]
    if record.specialties: lines.append("Specialties: " + ", ".join(record.specialties))
    if record.affiliations: lines.append("Affiliations: " + ", ".join(record.affiliations))
    for line in record.stock:
        state = "SOLD OUT" if line.sold_out or line.quantity <= 0 else f"x{line.quantity}"
        lines.append(f"{line.name} [{state}] {final_price(record, line):,.2f}")
    return PrintDocument(record.name, "Merchant", "\n".join(lines))
