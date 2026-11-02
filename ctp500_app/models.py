from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4


PLACEHOLDERS = frozenset({"", "-", "none"})


@dataclass(frozen=True, slots=True)
class GearItem:
    category: str
    source_gid: int
    source_row: int
    fields: tuple[tuple[str, str], ...]
    name: str
    manufacturer: str
    kind: str
    rank: str
    price: str

    @property
    def field_map(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.fields))

    @property
    def search_text(self) -> str:
        return " ".join((self.category, *(value for _, value in self.fields))).casefold()

    @property
    def identity(self) -> tuple[int, int, tuple[tuple[str, str], ...]]:
        return self.source_gid, self.source_row, self.fields


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    items: tuple[GearItem, ...]
    fetched_at: datetime
    digest: str


@dataclass(frozen=True, slots=True)
class PrintDocument:
    """A named printable payload which is independent of the live catalog."""

    name: str
    category: str
    text: str


class JobStatus(StrEnum):
    PENDING = "Pending"
    PRINTING = "Printing"
    FAILED = "Failed"
    COMPLETED = "Completed"
    CANCELED = "Canceled"


@dataclass(slots=True)
class PrintJob:
    item: GearItem | PrintDocument
    total_copies: int
    remaining_copies: int
    id: str = field(default_factory=lambda: str(uuid4()))
    status: JobStatus = JobStatus.PENDING
    error: str = ""
    cancel_requested: bool = False

    @classmethod
    def create(cls, item: GearItem | PrintDocument, copies: int) -> "PrintJob":
        if not 1 <= copies <= 99:
            raise ValueError("Copies must be between 1 and 99")
        return cls(item=item, total_copies=copies, remaining_copies=copies)
