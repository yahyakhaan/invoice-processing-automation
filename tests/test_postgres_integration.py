from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from invoice_agent.config import Settings
from invoice_agent.persistence.database import get_engine
from invoice_agent.runner import process_demo, resume_review
from invoice_agent.storage import (
    clear_processed_ledger,
    connect,
    ensure_database,
    list_run_snapshots,
    migrate_sqlite_data,
    record_human_decision,
    record_payment_attempt,
    record_processed,
)
from invoice_api.app import create_app
from invoice_api.service import RunService
from invoice_api.sources import PreparedSource

pytestmark = pytest.mark.postgres


def _wait_for_api_status(
    client: TestClient,
    run_id: str,
    expected: set[str],
    *,
    timeout: float = 8.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in expected:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {sorted(expected)}")


@pytest.fixture
def postgres_settings(tmp_path) -> Settings:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    settings = Settings(
        database_url=database_url,
        checkpoint_backend="postgres",
        langgraph_aes_key="phase-one-test-key-32-bytes-long",
        output_dir=tmp_path / "outputs",
        provider_override="mock",
        llm_provider="mock",
        owner_id="phase-one-integration-test",
    )
    ensure_database(settings)
    clear_processed_ledger(settings)
    return settings


def test_postgres_history_checkpoint_resume_and_idempotency(postgres_settings) -> None:
    pending = process_demo(postgres_settings, postgres_settings.output_dir)
    assert pending["outcome"] == "PENDING_VP_REVIEW"

    history = list_run_snapshots(postgres_settings)
    saved = next(item for item in history if item["thread_id"] == pending["thread_id"])
    assert saved["outcome"] == "PENDING_VP_REVIEW"

    resumed = resume_review(
        postgres_settings,
        pending["thread_id"],
        "approve",
        "VP Integration Test",
        "Approved after durable restart",
        postgres_settings.output_dir,
    )
    assert resumed["outcome"] == "PAY"
    assert resumed["payment"]["status"] == "success"

    replayed = resume_review(
        postgres_settings,
        pending["thread_id"],
        "approve",
        "VP Integration Test",
        "Repeated approval must remain idempotent",
        postgres_settings.output_dir,
    )
    assert replayed["outcome"] == "PAY"
    assert replayed["payment"]["idempotency_key"] == resumed["payment"]["idempotency_key"]

    with get_engine(postgres_settings.database_url).connect() as conn:
        persisted = conn.execute(
            text(
                "SELECT r.status, i.outcome "
                "FROM app.runs r JOIN app.invoices i ON i.run_id = r.id "
                "WHERE i.thread_id = CAST(:thread_id AS uuid)"
            ),
            {"thread_id": pending["thread_id"]},
        ).mappings().one()
        assert persisted == {"status": "COMPLETED", "outcome": "PAY"}

        review_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM app.human_reviews "
                "WHERE thread_id = CAST(:thread_id AS uuid)"
            ),
            {"thread_id": pending["thread_id"]},
        ).scalar_one()
        assert review_count == 1

        encrypted_blobs = conn.execute(
            text("SELECT COUNT(*) FROM checkpoint_blobs WHERE type LIKE '%+aes'")
        ).scalar_one()
        assert encrypted_blobs > 0


def test_legacy_sqlite_data_migrates_to_postgres(postgres_settings, tmp_path) -> None:
    sqlite_settings = Settings(
        inventory_db=tmp_path / "legacy.db",
        checkpoint_db=tmp_path / "legacy-checkpoints.db",
        provider_override="mock",
    )
    ensure_database(sqlite_settings)
    conn = connect(sqlite_settings)
    try:
        record_processed(
            conn,
            invoice_number="LEGACY-MIGRATE-001",
            revision=None,
            source_path="legacy.pdf",
            canonical_hash="legacy-hash",
            outcome="PAY",
            run_id="legacy-run",
        )
        record_human_decision(
            conn,
            thread_id="legacy-thread",
            decision="approve",
            actor="Legacy VP",
            rationale="Migrated record",
        )
        record_payment_attempt(
            conn,
            key="legacy-payment-key",
            invoice_number="LEGACY-MIGRATE-001",
            amount=125.50,
            vendor="Legacy Vendor",
            status="success",
            response={"status": "success"},
        )
    finally:
        conn.close()

    counts = migrate_sqlite_data(postgres_settings, sqlite_settings.inventory_db)
    assert counts["inventory"] == 4
    assert counts["processed_invoices"] == 1
    assert counts["human_decisions"] == 1
    assert counts["payment_attempts"] == 1

    with get_engine(postgres_settings.database_url).connect() as pg_conn:
        migrated = pg_conn.execute(
            text(
                "SELECT outcome FROM app.processed_invoices "
                "WHERE invoice_number = 'LEGACY-MIGRATE-001'"
            )
        ).scalar_one()
        assert migrated == "PAY"
        response = pg_conn.execute(
            text(
                "SELECT response FROM app.payment_attempts "
                "WHERE idempotency_key = 'legacy-payment-key'"
            )
        ).scalar_one()
        assert response == {"status": "success"}


def test_api_history_events_and_review_survive_app_restart(postgres_settings) -> None:
    with TestClient(create_app(postgres_settings)) as client:
        created_response = client.post("/api/runs", data={"sample_id": "demo:vp-review"})
        assert created_response.status_code == 202
        created = created_response.json()
        pending = _wait_for_api_status(
            client,
            created["run_id"],
            {"PENDING_REVIEW", "FAILED"},
        )
        assert pending["status"] == "PENDING_REVIEW"
        first_events = client.get(created["events_url"]).json()["items"]
        assert first_events[-1]["event_type"] == "RUN_PENDING_REVIEW"

    # A fresh app has no in-memory run registry and must recover from PostgreSQL.
    with TestClient(create_app(postgres_settings)) as restarted_client:
        recovered = restarted_client.get(created["detail_url"])
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "PENDING_REVIEW"
        assert recovered.json()["outcome"] is None

        review = restarted_client.post(
            created["review_url"],
            json={
                "decision": "approve",
                "actor": "VP Restart Test",
                "rationale": "Checkpoint and events recovered from PostgreSQL",
            },
        )
        assert review.status_code == 202
        completed = _wait_for_api_status(
            restarted_client,
            created["run_id"],
            {"COMPLETED", "FAILED"},
        )
        assert completed["status"] == "COMPLETED"
        assert completed["outcome"] == "PAY"
        assert completed["thread_id"] == created["thread_id"]

        events = restarted_client.get(created["events_url"]).json()["items"]
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert events[-1]["event_type"] == "RUN_FINISHED"
        assert all("phase-one-test-key" not in str(event) for event in events)


def test_public_demo_llm_allowance_survives_app_restart(postgres_settings) -> None:
    live_settings = postgres_settings.model_copy(
        update={
            "owner_id": "phase-five-quota-integration-test",
            "provider_override": "groq",
            "llm_provider": "groq",
            "groq_api_key": "test-only-key",
            "demo_llm_daily_limit": 2,
        }
    )
    source = PreparedSource(path=None, label="demo:quota", demo=True)

    first_service = RunService(live_settings)
    assert first_service.create(source)["agentic_mode"] is True

    restarted_service = RunService(live_settings)
    assert restarted_service.demo_status()["llm_runs_remaining"] == 1
    assert restarted_service.create(source)["agentic_mode"] is True
    fallback = restarted_service.create(source)
    assert fallback["agentic_mode"] is False
    assert fallback["provider"] == "mock"
