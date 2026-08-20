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