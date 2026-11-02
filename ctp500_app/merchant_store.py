from __future__ import annotations

import json
from dataclasses import asdict
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import app_data_dir
from .merchants import MerchantRecord, MerchantTransaction


class MerchantStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_dir() / "characters.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS merchants (id TEXT PRIMARY KEY, record_type TEXT NOT NULL, name TEXT NOT NULL, updated_at TEXT NOT NULL, document_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_merchants_name ON merchants(name COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS merchant_transactions (id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, timestamp TEXT NOT NULL, document_json TEXT NOT NULL, FOREIGN KEY(merchant_id) REFERENCES merchants(id) ON DELETE CASCADE);
                CREATE INDEX IF NOT EXISTS idx_merchant_transactions_merchant ON merchant_transactions(merchant_id,timestamp DESC);
            """)

    def _connect(self):
        db = sqlite3.connect(self.path); db.row_factory = sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); return db

    @contextmanager
    def _database(self):
        db = self._connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def save(self, record: MerchantRecord) -> None:
        document = json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._database() as db:
            db.execute("INSERT INTO merchants VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET record_type=excluded.record_type,name=excluded.name,updated_at=excluded.updated_at,document_json=excluded.document_json", (record.id, record.record_type, record.name, record.updated_at, document))

    def get(self, record_id: str) -> MerchantRecord | None:
        with self._database() as db: row = db.execute("SELECT document_json FROM merchants WHERE id=?", (record_id,)).fetchone()
        return MerchantRecord.from_dict(json.loads(row[0])) if row else None

    def list(self, query: str = "", record_type: str = "") -> list[MerchantRecord]:
        clauses, values = ["name LIKE ?"], [f"%{query.strip()}%"]
        if record_type: clauses.append("record_type=?"); values.append(record_type)
        with self._database() as db: rows = db.execute(f"SELECT document_json FROM merchants WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC", values).fetchall()
        return [MerchantRecord.from_dict(json.loads(row[0])) for row in rows]

    def delete(self, record_id: str) -> bool:
        with self._database() as db: return db.execute("DELETE FROM merchants WHERE id=?", (record_id,)).rowcount > 0

    def add_transaction(self, merchant_id: str, transaction: MerchantTransaction) -> None:
        with self._database() as db: db.execute("INSERT INTO merchant_transactions VALUES(?,?,?,?)", (transaction.id, merchant_id, transaction.timestamp, json.dumps(asdict(transaction), separators=(",", ":"))))

    def save_with_transaction(self, record: MerchantRecord, transaction: MerchantTransaction) -> None:
        """Persist a merchant and its ledger entry together.

        A newly created merchant has no parent row yet, so inserting its ledger
        entry first violates the transaction table's foreign-key constraint.
        """
        document = json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"))
        transaction_document = json.dumps(asdict(transaction), separators=(",", ":"))
        with self._database() as db:
            db.execute("INSERT INTO merchants VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET record_type=excluded.record_type,name=excluded.name,updated_at=excluded.updated_at,document_json=excluded.document_json", (record.id, record.record_type, record.name, record.updated_at, document))
            db.execute("INSERT INTO merchant_transactions VALUES(?,?,?,?)", (transaction.id, record.id, transaction.timestamp, transaction_document))

    def transactions(self, merchant_id: str) -> list[MerchantTransaction]:
        with self._database() as db: rows = db.execute("SELECT document_json FROM merchant_transactions WHERE merchant_id=? ORDER BY timestamp DESC", (merchant_id,)).fetchall()
        return [MerchantTransaction(**json.loads(row[0])) for row in rows]
