from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent.config import get_settings
from invoice_agent.storage import migrate_sqlite_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy legacy SQLite inventory and ledger rows to DATABASE_URL"
    )
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=ROOT / "db" / "inventory.db",
        help="Legacy SQLite database to copy",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    if not settings.database_url:
        print("error: DATABASE_URL must be set", file=sys.stderr)
        return 2
    if not args.sqlite_path.is_file():
        print(f"error: SQLite database not found: {args.sqlite_path}", file=sys.stderr)
        return 2
    counts = migrate_sqlite_data(settings, args.sqlite_path)
    print("Migrated legacy SQLite data to PostgreSQL:")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
