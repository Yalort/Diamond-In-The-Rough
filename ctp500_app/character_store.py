from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .characters import Character
from .config import app_data_dir


class CharacterStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_dir() / "characters.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        db = self._connect()
        try:
            db.execute(
                """CREATE TABLE IF NOT EXISTS characters (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    race TEXT NOT NULL,
                    role TEXT NOT NULL,
                    power_level INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    document_json TEXT NOT NULL
                )"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_characters_name ON characters(name COLLATE NOCASE)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_characters_race ON characters(race COLLATE NOCASE)")
            current = int(db.execute("PRAGMA user_version").fetchone()[0])
            if current < 1:
                db.execute("PRAGMA user_version = 1")
            db.commit()
        finally:
            db.close()

    def save(self, character: Character) -> None:
        document = json.dumps(character.to_dict(), ensure_ascii=False, separators=(",", ":"))
        db = self._connect()
        try:
            db.execute(
                """INSERT INTO characters(id,name,race,role,power_level,created_at,updated_at,document_json)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,race=excluded.race,
                   role=excluded.role,power_level=excluded.power_level,
                   updated_at=excluded.updated_at,document_json=excluded.document_json""",
                (character.id, character.name, character.race, character.role, character.power_level,
                 character.created_at, character.updated_at, document),
            )
            db.commit()
        finally:
            db.close()

    def get(self, character_id: str) -> Character | None:
        db = self._connect()
        try:
            row = db.execute("SELECT document_json FROM characters WHERE id=?", (character_id,)).fetchone()
        finally:
            db.close()
        return Character.from_dict(json.loads(row[0])) if row else None

    def list(self, query: str = "") -> list[Character]:
        needle = f"%{query.strip()}%"
        db = self._connect()
        try:
            rows = db.execute(
                """SELECT document_json FROM characters
                   WHERE name LIKE ? OR race LIKE ? OR role LIKE ?
                   ORDER BY updated_at DESC""",
                (needle, needle, needle),
            ).fetchall()
        finally:
            db.close()
        return [Character.from_dict(json.loads(row[0])) for row in rows]

    def delete(self, character_id: str) -> bool:
        db = self._connect()
        try:
            cursor = db.execute("DELETE FROM characters WHERE id=?", (character_id,))
            changed = cursor.rowcount > 0
            db.commit()
            return changed
        finally:
            db.close()
