from invoice_agent.orchestration.graph import InvoicePipeline
from invoice_agent.services.demo import hitl_demo_invoice


def _seed(settings, thread_id: str) -> dict:
    invoice = hitl_demo_invoice(thread_id)
    return {
        "raw_path": None,
        "demo_mode": True,
        "skip_ingest": True,
        "invoice": invoice.model_dump(),
        "events": [],
        "tool_trace": [],
        "agent_messages": [],
        "ingest_retry_count": 0,
        "validation_retry_count": 0,
        "approval_revision_count": 0,
        "agentic_mode": False,
        "run_id": "testrun",
        "thread_id": thread_id,
        "provider": "mock",
        "model": "mock-deterministic",
        "model_calls": 0,
    }


def test_pending_then_approve_pays_once(settings):
    pipeline = InvoicePipeline(settings, memory_checkpointer=True)
    thread = "hitl-approve"
    values = pipeline.run(thread_id=thread, payload=_seed(settings, thread))
    assert values["outcome"] == "PENDING_VP_REVIEW"
    assert values.get("payment") is None
    resumed = pipeline.resume(thread_id=thread, decision="approve", actor="VP", rationale="ok")
    assert resumed["outcome"] == "PAY"
    assert resumed["payment"]["status"] == "success"
    again = pipeline.resume(thread_id=thread, decision="approve", actor="VP", rationale="again")
    assert again["payment"]["status"] == "success"


def test_pending_then_reject_does_not_pay(settings):
    pipeline = InvoicePipeline(settings, memory_checkpointer=True)
    thread = "hitl-reject"
    pipeline.run(thread_id=thread, payload=_seed(settings, thread))
    resumed = pipeline.resume(thread_id=thread, decision="reject", actor="VP", rationale="no")
    assert resumed["outcome"] == "REJECT"
    assert resumed.get("payment") is None
