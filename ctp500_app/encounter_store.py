from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .config import app_data_dir
from .encounters import (
    ActionCard,
    Campaign,
    EncounterEvent,
    EncounterSession,
    EncounterTemplate,
)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class EncounterStore:
    """SQLite persistence for campaigns, encounter documents, events and actions."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_dir() / "characters.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        db = self._connect()
        try:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    document_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_campaigns_name ON campaigns(name COLLATE NOCASE);

                CREATE TABLE IF NOT EXISTS encounter_templates (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    folder TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    document_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_encounter_templates_campaign ON encounter_templates(campaign_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS encounter_sessions (
                    id TEXT PRIMARY KEY,
                    template_id TEXT NOT NULL DEFAULT '',
                    campaign_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    round INTEGER NOT NULL,
                    event_cursor INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    document_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_encounter_sessions_campaign ON encounter_sessions(campaign_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_encounter_sessions_status ON encounter_sessions(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS encounter_events (
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY(session_id, sequence),
                    FOREIGN KEY(session_id) REFERENCES encounter_sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS character_actions (
                    character_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    PRIMARY KEY(character_id, action_id)
                );
                CREATE INDEX IF NOT EXISTS idx_character_actions_character ON character_actions(character_id, name COLLATE NOCASE);
                """
            )
            current = int(db.execute("PRAGMA user_version").fetchone()[0])
            if current < 2:
                db.execute("PRAGMA user_version=2")
            db.commit()
        finally:
            db.close()

    def save_campaign(self, campaign: Campaign) -> None:
        db = self._connect()
        try:
            db.execute(
                """INSERT INTO campaigns(id,name,updated_at,document_json) VALUES(?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at,document_json=excluded.document_json""",
                (campaign.id, campaign.name, campaign.updated_at, _json(asdict(campaign))),
            )
            db.commit()
        finally:
            db.close()

    def get_campaign(self, campaign_id: str) -> Campaign | None:
        return self._get_document("campaigns", campaign_id, Campaign.from_dict)

    def list_campaigns(self, query: str = "") -> list[Campaign]:
        rows = self._list_rows("campaigns", query)
        return [Campaign.from_dict(json.loads(row["document_json"])) for row in rows]

    def delete_campaign(self, campaign_id: str) -> bool:
        db = self._connect()
        try:
            changed = db.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,)).rowcount > 0
            for table in ("encounter_templates", "encounter_sessions"):
                rows = db.execute(f"SELECT id,document_json FROM {table} WHERE campaign_id=?", (campaign_id,)).fetchall()
                for row in rows:
                    document = json.loads(row["document_json"])
                    document["campaign_id"] = ""
                    db.execute(f"UPDATE {table} SET campaign_id='',document_json=? WHERE id=?", (_json(document), row["id"]))
            db.commit()
            return changed
        finally:
            db.close()

    def save_template(self, template: EncounterTemplate) -> None:
        db = self._connect()
        try:
            db.execute(
                """INSERT INTO encounter_templates(id,campaign_id,name,folder,updated_at,document_json) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET campaign_id=excluded.campaign_id,name=excluded.name,
                   folder=excluded.folder,updated_at=excluded.updated_at,document_json=excluded.document_json""",
                (template.id, template.campaign_id, template.name, template.folder, template.updated_at, _json(asdict(template))),
            )
            db.commit()
        finally:
            db.close()

    def get_template(self, template_id: str) -> EncounterTemplate | None:
        return self._get_document("encounter_templates", template_id, EncounterTemplate.from_dict)

    def list_templates(self, query: str = "", campaign_id: str | None = None) -> list[EncounterTemplate]:
        db = self._connect()
        try:
            needle = f"%{query.strip()}%"
            if campaign_id is None:
                rows = db.execute("SELECT document_json FROM encounter_templates WHERE name LIKE ? OR folder LIKE ? ORDER BY updated_at DESC", (needle, needle)).fetchall()
            else:
                rows = db.execute("SELECT document_json FROM encounter_templates WHERE campaign_id=? AND (name LIKE ? OR folder LIKE ?) ORDER BY updated_at DESC", (campaign_id, needle, needle)).fetchall()
        finally:
            db.close()
        return [EncounterTemplate.from_dict(json.loads(row[0])) for row in rows]

    def delete_template(self, template_id: str) -> bool:
        return self._delete("encounter_templates", template_id)

    def save_session(self, session: EncounterSession) -> None:
        db = self._connect()
        try:
            self._upsert_session(db, session)
            db.commit()
        finally:
            db.close()

    def get_session(self, session_id: str) -> EncounterSession | None:
        return self._get_document("encounter_sessions", session_id, EncounterSession.from_dict)

    def list_sessions(self, query: str = "", campaign_id: str | None = None, status: str | None = None) -> list[EncounterSession]:
        db = self._connect()
        try:
            clauses = ["name LIKE ?"]
            values: list[object] = [f"%{query.strip()}%"]
            if campaign_id is not None:
                clauses.append("campaign_id=?")
                values.append(campaign_id)
            if status:
                clauses.append("status=?")
                values.append(status)
            rows = db.execute(f"SELECT document_json FROM encounter_sessions WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC", values).fetchall()
        finally:
            db.close()
        return [EncounterSession.from_dict(json.loads(row[0])) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        return self._delete("encounter_sessions", session_id)

    def commit_event(self, session: EncounterSession, event: EncounterEvent) -> EncounterEvent:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            cursor = int(db.execute("SELECT event_cursor FROM encounter_sessions WHERE id=?", (session.id,)).fetchone()[0]) if db.execute("SELECT 1 FROM encounter_sessions WHERE id=?", (session.id,)).fetchone() else 0
            db.execute("DELETE FROM encounter_events WHERE session_id=? AND sequence>?", (session.id, cursor))
            event.sequence = cursor + 1
            session.event_cursor = event.sequence
            self._upsert_session(db, session)
            db.execute(
                "INSERT INTO encounter_events(session_id,sequence,id,timestamp,event_type,summary,event_json) VALUES(?,?,?,?,?,?,?)",
                (session.id, event.sequence, event.id, event.timestamp, event.event_type, event.summary, _json(asdict(event))),
            )
            db.commit()
            return event
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_events(self, session_id: str) -> list[EncounterEvent]:
        db = self._connect()
        try:
            rows = db.execute("SELECT event_json FROM encounter_events WHERE session_id=? ORDER BY sequence", (session_id,)).fetchall()
        finally:
            db.close()
        return [EncounterEvent.from_dict(json.loads(row[0])) for row in rows]

    def undo(self, session_id: str) -> EncounterSession | None:
        session = self.get_session(session_id)
        if not session or session.event_cursor <= 0:
            return None
        db = self._connect()
        try:
            row = db.execute("SELECT event_json FROM encounter_events WHERE session_id=? AND sequence=?", (session_id, session.event_cursor)).fetchone()
            if not row:
                return None
            event = EncounterEvent.from_dict(json.loads(row[0]))
            restored = EncounterSession.from_dict(event.before)
            restored.event_cursor = session.event_cursor - 1
            restored.updated_at = session.updated_at
            self._upsert_session(db, restored)
            db.commit()
            return restored
        finally:
            db.close()

    def redo(self, session_id: str) -> EncounterSession | None:
        session = self.get_session(session_id)
        if not session:
            return None
        sequence = session.event_cursor + 1
        db = self._connect()
        try:
            row = db.execute("SELECT event_json FROM encounter_events WHERE session_id=? AND sequence=?", (session_id, sequence)).fetchone()
            if not row:
                return None
            event = EncounterEvent.from_dict(json.loads(row[0]))
            restored = EncounterSession.from_dict(event.after)
            restored.event_cursor = sequence
            self._upsert_session(db, restored)
            db.commit()
            return restored
        finally:
            db.close()

    def save_action(self, character_id: str, action: ActionCard) -> None:
        db = self._connect()
        try:
            db.execute(
                """INSERT INTO character_actions(character_id,action_id,name,document_json) VALUES(?,?,?,?)
                   ON CONFLICT(character_id,action_id) DO UPDATE SET name=excluded.name,document_json=excluded.document_json""",
                (character_id, action.id, action.name, _json(asdict(action))),
            )
            db.commit()
        finally:
            db.close()

    def list_actions(self, character_id: str) -> list[ActionCard]:
        db = self._connect()
        try:
            rows = db.execute("SELECT document_json FROM character_actions WHERE character_id=? ORDER BY name COLLATE NOCASE", (character_id,)).fetchall()
        finally:
            db.close()
        return [ActionCard.from_dict(json.loads(row[0])) for row in rows]

    def delete_action(self, character_id: str, action_id: str) -> bool:
        db = self._connect()
        try:
            changed = db.execute("DELETE FROM character_actions WHERE character_id=? AND action_id=?", (character_id, action_id)).rowcount > 0
            db.commit()
            return changed
        finally:
            db.close()

    def source_changed(self, combatant, character) -> bool:
        return bool(combatant.source_character_id and character and combatant.source_updated_at != character.updated_at)

    @staticmethod
    def refresh_preview(combatant, character) -> dict[str, tuple[object, object]]:
        before = combatant.character_snapshot or {}
        after = character.to_dict()
        return {key: (before.get(key), after.get(key)) for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)}

    @staticmethod
    def refresh_combatant(combatant, character) -> None:
        from .encounters import RulesEngine
        combatant.name = character.name or character.race
        combatant.power_level = character.power_level
        combatant.source_updated_at = character.updated_at
        combatant.character_snapshot = character.to_dict()
        manual = [action for action in combatant.actions if not RulesEngine.is_derived_action(action)]
        combatant.actions = RulesEngine.derive_actions(combatant) + manual

    def _upsert_session(self, db: sqlite3.Connection, session: EncounterSession) -> None:
        db.execute(
            """INSERT INTO encounter_sessions(id,template_id,campaign_id,name,status,round,event_cursor,updated_at,document_json)
               VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET template_id=excluded.template_id,
               campaign_id=excluded.campaign_id,name=excluded.name,status=excluded.status,round=excluded.round,
               event_cursor=excluded.event_cursor,updated_at=excluded.updated_at,document_json=excluded.document_json""",
            (session.id, session.template_id, session.campaign_id, session.name, session.status, session.round, session.event_cursor, session.updated_at, _json(asdict(session))),
        )

    def _get_document(self, table: str, record_id: str, factory):
        db = self._connect()
        try:
            row = db.execute(f"SELECT document_json FROM {table} WHERE id=?", (record_id,)).fetchone()
        finally:
            db.close()
        return factory(json.loads(row[0])) if row else None

    def _list_rows(self, table: str, query: str):
        db = self._connect()
        try:
            return db.execute(f"SELECT document_json FROM {table} WHERE name LIKE ? ORDER BY updated_at DESC", (f"%{query.strip()}%",)).fetchall()
        finally:
            db.close()

    def _delete(self, table: str, record_id: str) -> bool:
        db = self._connect()
        try:
            changed = db.execute(f"DELETE FROM {table} WHERE id=?", (record_id,)).rowcount > 0
            db.commit()
            return changed
        finally:
            db.close()
