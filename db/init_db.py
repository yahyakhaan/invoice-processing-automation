from __future__ import annotations

from invoice_agent.config import get_settings
from invoice_agent.storage import ensure_database

if __name__ == "__main__":
    path = ensure_database(get_settings())
    print(f"Initialized inventory database at {path}")
