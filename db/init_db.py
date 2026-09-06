from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent.config import get_settings
from invoice_agent.storage import ensure_database

if __name__ == "__main__":
    settings = get_settings()
    path = ensure_database(settings)
    if settings.uses_postgres:
        print("Initialized PostgreSQL database")
    else:
        print(f"Initialized inventory database at {path}")
