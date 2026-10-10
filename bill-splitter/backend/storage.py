"""SQLite storage for bills, items, people, and claims (data/bills.db)."""

import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from backend.models import ParsedReceipt

DEFAULT_DB = Path(__file__).parent.parent / "data" / "bills.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    image_hash TEXT,
    tax_cents INTEGER NOT NULL DEFAULT 0,
    tip_cents INTEGER NOT NULL DEFAULT 0,
    receipt_total_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    override_cents INTEGER
);
CREATE TABLE IF NOT EXISTS claims (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, person_id)
);
"""


def db_path() -> Path:
    return Path(os.getenv("BILLS_DB", DEFAULT_DB))


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def create_bill(parsed: ParsedReceipt, image_hash: str) -> int:
    name = parsed.merchant or f"Bill – {date.today().isoformat()}"
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO bills (name, created_at, image_hash, tax_cents, tip_cents, receipt_total_cents)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name, created_at, image_hash, parsed.tax_cents, parsed.tip_cents, parsed.total_cents),
        )
        bill_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO items (bill_id, position, name, quantity, price_cents) VALUES (?, ?, ?, ?, ?)",
            [(bill_id, pos, it.name, it.quantity, it.price_cents) for pos, it in enumerate(parsed.items)],
        )
    return bill_id


def get_bill_rows(bill_id: int) -> dict | None:
    """Raw rows for one bill, or None if it doesn't exist."""
    with connect() as conn:
        bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if bill is None:
            return None
        items = conn.execute(
            "SELECT * FROM items WHERE bill_id = ? ORDER BY position, id", (bill_id,)
        ).fetchall()
        people = conn.execute(
            "SELECT * FROM people WHERE bill_id = ? ORDER BY position, id", (bill_id,)
        ).fetchall()
        claims = conn.execute(
            "SELECT c.item_id, c.person_id FROM claims c JOIN items i ON i.id = c.item_id"
            " WHERE i.bill_id = ?",
            (bill_id,),
        ).fetchall()
    return {
        "bill": dict(bill),
        "items": [dict(r) for r in items],
        "people": [dict(r) for r in people],
        "claims": [dict(r) for r in claims],
    }
