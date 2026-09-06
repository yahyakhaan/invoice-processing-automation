from __future__ import annotations

from uuid import UUID

import pytest
from alembic import command
from pydantic import ValidationError

from invoice_agent.config import Settings
from invoice_agent.llm import get_chat_model
from invoice_agent.orchestration.graph import InvoicePipeline
from invoice_agent.persistence.database import alembic_config
from invoice_agent.persistence.models import Base
from invoice_agent.runner import process_path
from invoice_agent.storage import record_run_snapshot


def test_postgres_settings_select_postgres_checkpointer() -> None:
    settings = Settings(database_url="postgresql://user:pass@db.example/invoices")
    assert settings.uses_postgres is True
    assert settings.resolved_checkpoint_backend == "postgres"


def test_sqlite_remains_default_backend() -> None:
    settings = Settings()
    assert settings.uses_postgres is False
    assert settings.resolved_checkpoint_backend == "sqlite"


@pytest.mark.parametrize(
    "database_url",
    ["sqlite:///tmp/test.db", "mysql://user:pass@localhost/test", "not-a-url"],
)
def test_database_url_rejects_non_postgres(database_url: str) -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL must use PostgreSQL"):
        Settings(database_url=database_url)


def test_checkpoint_encryption_key_length_is_validated() -> None:
    with pytest.raises(ValidationError, match="16, 24, or 32 bytes"):
        Settings(langgraph_aes_key="too-short")
    assert Settings(langgraph_aes_key="x" * 32).langgraph_aes_key == "x" * 32


def test_secret_settings_are_excluded_from_repr_and_serialization() -> None:
    settings = Settings(
        database_url="postgresql://db-user:db-password@db.example/invoices",
        langgraph_aes_key="x" * 32,
        groq_api_key="groq-secret",
        xai_api_key="xai-secret",
        openai_api_key="openai-secret",
        anthropic_api_key="anthropic-secret",
    )
    rendered = repr(settings)
    serialized = settings.model_dump_json()
    for secret in [
        "db-password",
        "groq-secret",
        "xai-secret",
        "openai-secret",
        "anthropic-secret",
        "x" * 32,
    ]:
        assert secret not in rendered
        assert secret not in serialized


def test_groq_is_the_default_hosted_provider_when_configured() -> None:
    settings = Settings(groq_api_key="test-groq-key", llm_provider=None)
    assert settings.provider == "groq"
    assert settings.model_name == "llama-3.3-70b-versatile"
    assert settings.resolved_api_key() == "test-groq-key"
    model = get_chat_model(settings)
    assert model.model_name == "llama-3.3-70b-versatile"
    assert str(model.openai_api_base) == "https://api.groq.com/openai/v1"


def test_postgres_checkpointer_requires_encryption_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "invoice_agent.orchestration.graph.storage.ensure_database", lambda settings: None
    )
    settings = Settings(
        database_url="postgresql://user:pass@db.example/invoices",
        provider_override="mock",
    )
    with pytest.raises(ValueError, match="LANGGRAPH_AES_KEY is required"):
        InvoicePipeline(settings)


def test_application_metadata_contains_phase_one_tables() -> None:
    tables = {table.name for table in Base.metadata.tables.values()}
    assert {
        "inventory",
        "vendors",
        "fx_rates",
        "runs",
        "invoices",
        "validation_results",
        "approval_decisions",
        "run_events",
        "processed_invoices",
        "human_reviews",
        "payment_attempts",
    } <= tables
    assert {table.schema for table in Base.metadata.tables.values()} == {"app"}


def test_alembic_migration_renders_offline(capsys) -> None:
    command.upgrade(
        alembic_config("postgresql://user:pass@localhost/invoice_test"),
        "head",
        sql=True,
    )
    rendered = capsys.readouterr().out
    assert "CREATE SCHEMA IF NOT EXISTS app" in rendered
    assert "CREATE TABLE app.runs" in rendered
    assert "CREATE TABLE app.payment_attempts" in rendered
    assert "0001_postgres_foundation" in rendered


def test_sqlite_snapshot_persistence_is_a_noop(settings) -> None:
    record_run_snapshot(
        settings,
        {
            "run_id": "legacy-run",
            "thread_id": "legacy-thread",
            "provider": "mock",
            "model": "mock-deterministic",
            "outcome": "PAY",
            "events": [],
        },
    )


def test_runner_uses_uuid_run_and_thread_ids(settings, invoices) -> None:
    [result] = process_path(settings, str(invoices / "invoice_1001.txt"), settings.output_dir)
    assert str(UUID(result["run_id"])) == result["run_id"]
    assert str(UUID(result["thread_id"])) == result["thread_id"]
