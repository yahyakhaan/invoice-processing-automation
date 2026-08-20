from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from invoice_agent.config import ROOT, Settings
from invoice_agent.observability.console import render_console, write_result, append_event
from invoice_agent.orchestration.graph import InvoicePipeline
from invoice_agent.services.demo import hitl_demo_invoice
from invoice_agent.storage import ensure_database


def collect_invoices(path: str) -> list[Path]:
    target = Path(path)
    if not target.is_absolute():
        target = (ROOT / target).resolve()
    if target.is_file():
        return [target]
    files = [
        p
        for p in target.iterdir()
        if p.is_file() and p.suffix.lower() in {".txt", ".json", ".xml", ".csv", ".pdf"}
    ]
    ext_order = {".txt": 0, ".json": 1, ".xml": 2, ".csv": 3, ".pdf": 4}
    return sorted(files, key=lambda p: (p.stem, ext_order.get(p.suffix.lower(), 9)))


def result_payload(values: dict, settings: Settings, source: str | None) -> dict:
    invoice = values.get("invoice") or values.get("invoice_draft") or {}
    return {
        "source": source,
        "invoice_id": invoice.get("invoice_number"),
        "thread_id": values.get("thread_id"),
        "run_id": values.get("run_id"),
        "outcome": values.get("outcome"),
        "agentic_mode": settings.agentic_mode,
        "provider": settings.provider,
        "model": settings.model_name,
        "demo_mode": values.get("demo_mode", False),
        "invoice": values.get("invoice"),
        "validation": values.get("validation"),
        "approval_draft": values.get("approval_draft"),
        "approval_critique": values.get("approval_critique"),
        "approval_final": values.get("approval_final"),
        "human_review": values.get("human_review"),
        "payment": values.get("payment"),
        "pending_interrupt": values.get("pending_interrupt"),
        "model_calls": values.get("model_calls") or 0,
        "tool_calls": len(values.get("tool_trace") or []),
        "events": values.get("events") or [],
    }


def persist(values: dict, settings: Settings, output_root: Path, source: str | None) -> Path:
    invoice = values.get("invoice") or {}
    name = invoice.get("invoice_number") or "unknown"
    stem = Path(source).stem if source else name
    folder = output_root / values["run_id"] / stem
    folder.mkdir(parents=True, exist_ok=True)
    payload = result_payload(values, settings, source)
    write_result(folder / "result.json", payload)
    events_path = folder / "events.jsonl"
    if events_path.exists():
        events_path.unlink()
    for item in values.get("events") or []:
        append_event(events_path, item)
    return folder / "result.json"


def print_resume_help(thread_id: str) -> None:
    print()
    print("PENDING VP REVIEW — invoice exceeds policy or needs human scrutiny.")
    print(f"thread_id: {thread_id}")
    print("Resume with one of:")
    print(
        f'  python main.py --resume={thread_id} --vp-decision=approve --vp-actor="VP Demo" --vp-reason="Approved after review"'
    )
    print(
        f'  python main.py --resume={thread_id} --vp-decision=reject --vp-actor="VP Demo" --vp-reason="Declined"'
    )


def process_path(settings: Settings, invoice_path: str, output_dir: Path) -> list[dict]:
    ensure_database(settings)
    pipeline = InvoicePipeline(settings)
    results = []
    run_id = uuid4().hex[:12]
    for path in collect_invoices(invoice_path):
        thread_id = f"{run_id}-{path.stem}-{path.suffix.lstrip('.')}"
        values = pipeline.run(
            thread_id=thread_id,
            payload={
                "raw_path": str(path),
                "demo_mode": False,
                "events": [],
                "tool_trace": [],
                "agent_messages": [],
                "ingest_retry_count": 0,
                "validation_retry_count": 0,
                "approval_revision_count": 0,
                "agentic_mode": settings.agentic_mode,
                "run_id": run_id,
                "thread_id": thread_id,
                "provider": settings.provider,
                "model": settings.model_name,
                "model_calls": 0,
                "skip_ingest": False,
            },
        )
        payload = result_payload(values, settings, str(path))
        persist(values, settings, output_dir, str(path))
        render_console(payload)
        if payload.get("outcome") == "PENDING_VP_REVIEW":
            print_resume_help(thread_id)
        results.append(payload)
    return results


def process_demo(settings: Settings, output_dir: Path) -> dict:
    ensure_database(settings)
    pipeline = InvoicePipeline(settings)
    run_id = uuid4().hex[:12]
    thread_id = f"demo-{run_id}"
    invoice = hitl_demo_invoice(thread_id)
    values = pipeline.run(
        thread_id=thread_id,
        payload={
            "raw_path": None,
            "demo_mode": True,
            "skip_ingest": True,
            "invoice": invoice.model_dump(),
            "invoice_draft": None,
            "events": [],
            "tool_trace": [],
            "agent_messages": [],
            "ingest_retry_count": 0,
            "validation_retry_count": 0,
            "approval_revision_count": 0,
            "agentic_mode": settings.agentic_mode,
            "run_id": run_id,
            "thread_id": thread_id,
            "provider": settings.provider,
            "model": settings.model_name,
            "model_calls": 0,
        },
    )
    payload = result_payload(values, settings, "demo:vp-review")
    persist(values, settings, output_dir, "demo_vp_review")
    render_console(payload)
    print_resume_help(thread_id)
    return payload


def resume_review(settings: Settings, thread_id: str, decision: str, actor: str, rationale: str, output_dir: Path) -> dict:
    ensure_database(settings)
    pipeline = InvoicePipeline(settings)
    values = pipeline.resume(thread_id=thread_id, decision=decision, actor=actor, rationale=rationale)
    payload = result_payload(values, settings, values.get("raw_path"))
    persist(values, settings, output_dir, values.get("raw_path") or thread_id)
    render_console(payload)
    return payload
