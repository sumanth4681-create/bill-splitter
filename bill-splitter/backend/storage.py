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
    discount_cents INTEGER NOT NULL DEFAULT 0,
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
        # Bills created before the discount field existed get the column added in place.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(bills)")}
        if "discount_cents" not in columns:
            conn.execute("ALTER TABLE bills ADD COLUMN discount_cents INTEGER NOT NULL DEFAULT 0")


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


def replace_people(bill_id: int, names: list[str]) -> None:
    """Set the bill's people to `names` in order. People whose name is unchanged keep
    their id (and claims); removed people lose their claims; new names are added."""
    wanted = []
    for raw in names:
        name = " ".join(raw.split())
        if name and name.lower() not in {w.lower() for w in wanted}:
            wanted.append(name)
    with connect() as conn:
        existing = {r["name"].lower(): r["id"] for r in conn.execute(
            "SELECT id, name FROM people WHERE bill_id = ?", (bill_id,))}
        keep = {existing[n.lower()] for n in wanted if n.lower() in existing}
        gone = [pid for pid in existing.values() if pid not in keep]
        if gone:
            marks = ",".join("?" * len(gone))
            conn.execute(f"DELETE FROM claims WHERE person_id IN ({marks})", gone)
            conn.execute(f"DELETE FROM people WHERE id IN ({marks})", gone)
        for pos, name in enumerate(wanted):
            if name.lower() in existing:
                conn.execute("UPDATE people SET position = ?, name = ? WHERE id = ?",
                             (pos, name, existing[name.lower()]))
            else:
                conn.execute("INSERT INTO people (bill_id, position, name) VALUES (?, ?, ?)",
                             (bill_id, pos, name))


def set_claim(item_id: int, person_id: int, claimed: bool) -> None:
    with connect() as conn:
        if claimed:
            conn.execute("INSERT OR IGNORE INTO claims (item_id, person_id) VALUES (?, ?)",
                         (item_id, person_id))
        else:
            conn.execute("DELETE FROM claims WHERE item_id = ? AND person_id = ?", (item_id, person_id))


def _update(conn: sqlite3.Connection, table: str, row_id: int, fields: dict) -> None:
    changes = {k: v for k, v in fields.items() if v is not None}
    if not changes:
        return
    assignments = ", ".join(f"{k} = ?" for k in changes)
    conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", (*changes.values(), row_id))


def update_bill(bill_id: int, name: str | None = None, tax_cents: int | None = None, tip_cents: int | None = None,
                discount_cents: int | None = None, receipt_total_cents: int | None = None) -> None:
    with connect() as conn:
        _update(conn, "bills", bill_id, {"name": name, "tax_cents": tax_cents, "tip_cents": tip_cents,
                                         "discount_cents": discount_cents, "receipt_total_cents": receipt_total_cents})


def update_item(item_id: int, quantity: int | None = None, price_cents: int | None = None) -> None:
    with connect() as conn:
        _update(conn, "items", item_id, {"quantity": quantity, "price_cents": price_cents})


def set_override(person_id: int, amount_cents: int | None) -> None:
    """A person's hand-edited final amount; None clears it."""
    with connect() as conn:
        conn.execute("UPDATE people SET override_cents = ? WHERE id = ?", (amount_cents, person_id))
