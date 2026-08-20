from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from invoice_agent.config import ROOT, Settings, get_settings

SCHEMA_SQL = (ROOT / "data" / "seed_inventory.sql").read_text(encoding="utf-8")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_database(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = Path(settings.inventory_db)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    Path(settings.checkpoint_db).parent.mkdir(parents=True, exist_ok=True)
    return path


def lookup_item(conn: sqlite3.Connection, sku: str) -> dict | None:
    row = conn.execute("SELECT item, stock, unit_cost FROM inventory WHERE item = ?", (sku,)).fetchone()
    return dict(row) if row else None


def fx_rate(conn: sqlite3.Connection, currency: str, default_eur: float = 1.08) -> float:
    row = conn.execute(
        "SELECT usd_per_unit FROM fx_rates WHERE currency = ?", (currency.upper(),)
    ).fetchone()
    if row:
        return float(row["usd_per_unit"])
    if currency.upper() == "EUR":
        return default_eur
    return 1.0


def find_processed(conn: sqlite3.Connection, invoice_number: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM processed_invoices WHERE invoice_number = ? ORDER BY created_at",
        (invoice_number,),
    ).fetchall()
    return [dict(r) for r in rows]


def record_processed(
    conn: sqlite3.Connection,
    *,
    invoice_number: str,
    revision: str | None,
    source_path: str | None,
    canonical_hash: str | None,
    outcome: str,
    run_id: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO processed_invoices
        (invoice_number, revision, source_path, canonical_hash, outcome, run_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            invoice_number,
            revision or "",
            source_path or "",
            canonical_hash,
            outcome,
            run_id,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def record_human_decision(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    decision: str,
    actor: str,
    rationale: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO human_decisions
        (thread_id, decision, actor, rationale, decided_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (thread_id, decision, actor, rationale, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_payment_attempt(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute("SELECT * FROM payment_attempts WHERE idempotency_key = ?", (key,)).fetchone()
    return dict(row) if row else None


def record_payment_attempt(
    conn: sqlite3.Connection,
    *,
    key: str,
    invoice_number: str,
    amount: float,
    vendor: str,
    status: str,
    response: dict,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO payment_attempts
        (idempotency_key, invoice_number, amount, vendor, status, response, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            invoice_number,
            amount,
            vendor,
            status,
            json.dumps(response),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
