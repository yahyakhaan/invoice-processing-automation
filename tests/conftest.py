from pathlib import Path

import pytest

from invoice_agent.config import Settings
from invoice_agent.storage import ensure_database

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path) -> Settings:
    cfg = Settings(
        inventory_db=tmp_path / "inventory.db",
        checkpoint_db=tmp_path / "checkpoints.db",
        output_dir=tmp_path / "outputs",
        provider_override="mock",
        llm_provider="mock",
    )
    ensure_database(cfg)
    return cfg


@pytest.fixture
def invoices() -> Path:
    return ROOT / "data" / "invoices"
