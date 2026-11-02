"""Permanent record of every file the app has handled (SQLite).

The ledger - not the Excel file - is what makes the app safe to run repeatedly:
it remembers file fingerprints and supplier+invoice-number pairs, and which
records have already been written to the Excel register.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER, processed_at TEXT, original_name TEXT, stored_path TEXT, file_hash TEXT,
    status TEXT, supplier TEXT, supplier_key TEXT, invoice_number TEXT, invoice_number_key TEXT,
    invoice_date TEXT, due_date TEXT, subtotal REAL, vat REAL, total REAL, currency TEXT,
    method TEXT, issues TEXT, reason TEXT, duplicate_of INTEGER, in_workbook INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_hash ON records(file_hash);
CREATE INDEX IF NOT EXISTS ix_key ON records(supplier_key, invoice_number_key);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT, found INTEGER, processed INTEGER,
    review INTEGER, duplicates INTEGER, failed INTEGER, skipped INTEGER, excel_ok INTEGER, note TEXT
);
"""


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(records)")}
        for col, typ in (("line_items", "TEXT"), ("line_items_check", "TEXT"), ("language", "TEXT")):   # upgrade older ledgers
            if col not in cols:
                self.db.execute(f"ALTER TABLE records ADD COLUMN {col} {typ}")
        self.db.commit()

    def close(self):
        self.db.close()

    # ---- runs
    def start_run(self, started: str) -> int:
        cur = self.db.execute("INSERT INTO runs(started) VALUES (?)", (started,)); self.db.commit(); return cur.lastrowid

    def finish_run(self, run_id, **kw):
        cols = ", ".join(f"{k}=?" for k in kw)
        self.db.execute(f"UPDATE runs SET {cols} WHERE id=?", (*kw.values(), run_id)); self.db.commit()

    # ---- duplicate lookups (FAILED records are ignored: a failed file may be retried)
    def find_by_hash(self, h):
        return self.db.execute("SELECT * FROM records WHERE file_hash=? AND status IN ('PROCESSED','NEEDS_REVIEW') ORDER BY id LIMIT 1", (h,)).fetchone()

    def find_by_invoice(self, supplier_key, number_key):
        if not supplier_key or not number_key:
            return None
        return self.db.execute("SELECT * FROM records WHERE supplier_key=? AND invoice_number_key=? AND status IN ('PROCESSED','NEEDS_REVIEW') ORDER BY id LIMIT 1",
                               (supplier_key, number_key)).fetchone()

    def find_similar(self, supplier_key, total, invoice_date):
        if not supplier_key or total is None or not invoice_date:
            return None
        return self.db.execute("SELECT * FROM records WHERE supplier_key=? AND ABS(total-?)<0.005 AND invoice_date=? AND status IN ('PROCESSED','NEEDS_REVIEW') ORDER BY id LIMIT 1",
                               (supplier_key, total, invoice_date)).fetchone()

    # ---- writes
    def add(self, rec: dict) -> int:
        rec = dict(rec); rec["issues"] = json.dumps(rec.get("issues", []))
        rec["line_items"] = json.dumps(rec["line_items"]) if rec.get("line_items") is not None else None
        cols = ", ".join(rec); q = ", ".join("?" * len(rec))
        cur = self.db.execute(f"INSERT INTO records({cols}) VALUES ({q})", tuple(rec.values()))
        return cur.lastrowid

    def delete(self, rid):
        self.db.execute("DELETE FROM records WHERE id=?", (rid,))

    def commit(self):
        self.db.commit()

    def pending_workbook(self):
        return [self._row(r) for r in self.db.execute("SELECT * FROM records WHERE in_workbook=0 ORDER BY id")]

    def mark_in_workbook(self, ids):
        self.db.executemany("UPDATE records SET in_workbook=1 WHERE id=?", [(i,) for i in ids]); self.db.commit()

    def reset_workbook_flags(self):
        self.db.execute("UPDATE records SET in_workbook=0"); self.db.commit()

    def runs(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM runs WHERE finished IS NOT NULL ORDER BY id")]

    @staticmethod
    def _row(r):
        d = dict(r); d["issues"] = json.loads(d["issues"] or "[]")
        d["line_items"] = json.loads(d["line_items"]) if d.get("line_items") else None
        return d
