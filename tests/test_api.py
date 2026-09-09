from __future__ import annotations

import time
from uuid import UUID

from fastapi.testclient import TestClient
from fpdf import FPDF

from invoice_api.app import create_app
from invoice_api.service import RunService, sanitize_event_data
from invoice_api.sources import PreparedSource


def wait_for_status(
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


def test_event_sanitizer_redacts_credentials_and_internal_paths() -> None:
    sanitized = sanitize_event_data(
        {
            "authorization": "Bearer secret",
            "nested": {"database-url": "postgresql://secret", "token": "secret"},
            "path": "/private/tmp/invoice-upload/file.pdf",
            "flags": ["OVER_STOCK"],
        }
    )
    assert sanitized == {
        "authorization": "[REDACTED]",
        "nested": {"database-url": "[REDACTED]", "token": "[REDACTED]"},
        "path": "file.pdf",
        "flags": ["OVER_STOCK"],
    }


def test_health_and_sample_catalog_do_not_expose_secrets(settings) -> None:
    settings.database_url = None
    settings.xai_api_key = "must-not-appear"
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok",
            "database_backend": "sqlite",
            "provider": "mock",
            "llm_enabled": False,
            "llm_daily_limit": 5,
            "llm_runs_remaining": 0,
        }
        assert "must-not-appear" not in health.text

        samples = client.get("/api/samples")
        assert samples.status_code == 200
        ids = {item["id"] for item in samples.json()}
        assert "demo:vp-review" in ids
        assert "invoice_1001.txt" in ids

        content = client.get("/api/samples/invoice_1001.txt/content")
        assert content.status_code == 200
        assert "INV-1001" in content.text
        assert client.get("/api/samples/..%2Fseed_inventory.sql/content").status_code == 404


def test_public_demo_falls_back_after_daily_llm_allowance(settings) -> None:
    live_settings = settings.model_copy(
        update={
            "provider_override": "groq",
            "llm_provider": "groq",
            "groq_api_key": "test-only-key",
            "demo_llm_daily_limit": 2,
        }
    )
    service = RunService(live_settings)
    source = PreparedSource(path=None, label="demo:quota", demo=True)

    records = [service.create(source) for _ in range(3)]

    assert [record["agentic_mode"] for record in records] == [True, True, False]
    assert [record["provider"] for record in records] == ["groq", "groq", "mock"]
    assert service.demo_status() == {
        "llm_enabled": True,
        "llm_daily_limit": 2,
        "llm_runs_remaining": 0,
    }


def test_compiled_frontend_is_served_without_shadowing_api(settings, tmp_path) -> None:
    frontend_dist = tmp_path / "frontend-dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text(
        "<!doctype html><title>Container UI</title>",
        encoding="utf-8",
    )

    with TestClient(create_app(settings, frontend_dist=frontend_dist)) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "Container UI" in root.text
        assert client.get("/api/health").status_code == 200


def test_sample_run_history_events_stream_and_artifact(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/runs", data={"sample_id": "invoice_1001.txt"})
        assert response.status_code == 202
        created = response.json()
        assert str(UUID(created["run_id"])) == created["run_id"]
        assert str(UUID(created["thread_id"])) == created["thread_id"]
        assert created["status"] == "CREATED"

        detail = wait_for_status(client, created["run_id"], {"COMPLETED", "FAILED"})
        assert detail["status"] == "COMPLETED"
        assert detail["outcome"] == "PAY"
        assert detail["invoice_id"] == "INV-1001"

        events_response = client.get(created["events_url"])
        assert events_response.status_code == 200
        events = events_response.json()["items"]
        sequences = [item["sequence"] for item in events]
        assert sequences == list(range(1, len(events) + 1))
        assert {item["event_type"] for item in events} >= {
            "RUN_CREATED",
            "RUN_STARTED",
            "RUN_FINISHED",
        }
        assert any(item["stage"] == "VALIDATE" for item in events)

        stream = client.get(created["stream_url"])
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event: run-event" in stream.text
        assert "RUN_FINISHED" in stream.text

        history = client.get("/api/runs").json()["items"]
        assert [item["run_id"] for item in history] == [created["run_id"]]

        artifact = client.get(f"/api/runs/{created['run_id']}/artifact")
        assert artifact.status_code == 200
        assert "attachment" in artifact.headers["content-disposition"]
        assert artifact.json()["outcome"] == "PAY"


def test_uploaded_invoice_and_input_validation(settings, tmp_path, monkeypatch) -> None:
    upload_directory = tmp_path / "temporary-upload"

    def create_upload_directory(*, prefix: str) -> str:
        assert prefix.startswith("invoice-upload-")
        upload_directory.mkdir()
        return str(upload_directory)

    monkeypatch.setattr("invoice_api.sources.tempfile.mkdtemp", create_upload_directory)
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/runs").status_code == 400
        assert (
            client.post(
                "/api/runs",
                data={"sample_id": "invoice_1001.txt"},
                files={"file": ("invoice.txt", b"content", "text/plain")},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/runs",
                files={"file": ("invoice.exe", b"content", "application/octet-stream")},
            ).status_code
            == 415
        )
        assert (
            client.post(
                "/api/runs",
                files={"file": ("empty.txt", b"", "text/plain")},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/runs",
                files={"file": ("../invoice.txt", b"content", "text/plain")},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/runs",
                files={"file": ("broken.pdf", b"not a pdf", "application/pdf")},
            ).status_code
            == 400
        )
        document = FPDF()
        document.add_page()
        document.set_font("Helvetica", size=12)
        document.cell(text="Page one")
        document.add_page()
        document.cell(text="Page two")
        settings.max_pdf_pages = 1
        assert (
            client.post(
                "/api/runs",
                files={"file": ("too-many-pages.pdf", bytes(document.output()), "application/pdf")},
            ).status_code
            == 413
        )
        settings.max_upload_mb = 1
        assert (
            client.post(
                "/api/runs",
                files={"file": ("too-large.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
            ).status_code
            == 413
        )

        source = b"""Invoice Number: API-UPLOAD-001
Vendor: QuickShip
Invoice Date: 2025-01-15
Due Date: 2025-02-15
Currency: USD
WidgetA | Qty: 1 | Unit Price: 250 | Amount: 250
Subtotal: 250
Tax: 0
Shipping: 0
Total: 250
"""
        response = client.post(
            "/api/runs",
            files={"file": ("uploaded-invoice.txt", source, "text/plain")},
        )
        assert response.status_code == 202
        detail = wait_for_status(client, response.json()["run_id"], {"COMPLETED", "FAILED"})
        assert detail["status"] == "COMPLETED"
        assert detail["source"] == "upload:uploaded-invoice.txt"
        assert not upload_directory.exists()


def test_vp_review_resumes_the_same_run(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/runs", data={"sample_id": "demo:vp-review"})
        assert response.status_code == 202
        created = response.json()
        pending = wait_for_status(client, created["run_id"], {"PENDING_REVIEW", "FAILED"})
        assert pending["status"] == "PENDING_REVIEW"
        assert pending["outcome"] is None
        assert pending["pending_interrupt"]["type"] == "vp_review"
        assert client.get(f"/api/runs/{created['run_id']}/artifact").status_code == 409

        review = client.post(
            created["review_url"],
            json={
                "decision": "approve",
                "actor": "VP API Test",
                "rationale": "Approved in the web workflow",
            },
        )
        assert review.status_code == 202
        completed = wait_for_status(client, created["run_id"], {"COMPLETED", "FAILED"})
        assert completed["status"] == "COMPLETED"
        assert completed["outcome"] == "PAY"
        assert completed["thread_id"] == created["thread_id"]
        assert completed["human_review"]["actor"] == "VP API Test"
        assert completed["payment"]["status"] == "success"

        duplicate_review = client.post(
            created["review_url"],
            json={"decision": "approve", "actor": "VP API Test", "rationale": "again"},
        )
        assert duplicate_review.status_code == 409
