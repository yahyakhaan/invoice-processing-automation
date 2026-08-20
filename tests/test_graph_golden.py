from pathlib import Path

from invoice_agent.orchestration.graph import InvoicePipeline
from invoice_agent.runner import collect_invoices

ROOT = Path(__file__).resolve().parents[1]
INV = ROOT / "data" / "invoices"

GOLDEN = {
    "invoice_1001.txt": {"pay": True, "flags": set()},
    "invoice_1002.txt": {"pay": False, "any": {"OVER_STOCK"}},
    "invoice_1003.txt": {"pay": False, "any": {"ZERO_STOCK", "UNKNOWN_ITEM", "MISSING_REQUIRED_FIELD", "FRAUD_LANGUAGE"}},
    "invoice_1004.json": {"pay": True, "flags": set()},
    "invoice_1004_revised.json": {"pay": True, "any": {"REVISION"}},
    "invoice_1005.json": {"pay": False, "any": {"OVER_STOCK"}},
    "invoice_1006.csv": {"pay": True, "flags": set()},
    "invoice_1007.csv": {"pay": False, "any": {"OVER_STOCK"}},
    "invoice_1008.txt": {"pay": False, "any": {"UNKNOWN_ITEM"}},
    "invoice_1009.json": {"pay": False, "any": {"DATA_INTEGRITY", "MISSING_REQUIRED_FIELD"}},
    "invoice_1010.txt": {"pay": True, "flags": set()},
    "invoice_1011.txt": {"pay": True, "flags": set()},
    "invoice_1012.txt": {"pay": True, "warn": {"VENDOR_IDENTITY", "OCR"}},
    "invoice_1013.json": {"pay": False, "any": {"OVER_STOCK", "TOTAL_MISMATCH"}},
    "invoice_1014.xml": {"pay": True, "warn": {"FX_REVIEW"}},
    "invoice_1015.csv": {"pay": True, "flags": set()},
    "invoice_1016.json": {"pay": False, "any": {"UNKNOWN_ITEM"}},
}


def _run_file(settings, path: Path):
    pipeline = InvoicePipeline(settings, memory_checkpointer=True)
    thread = path.stem
    return pipeline.run(
        thread_id=thread,
        payload={
            "raw_path": str(path),
            "events": [],
            "tool_trace": [],
            "agent_messages": [],
            "ingest_retry_count": 0,
            "validation_retry_count": 0,
            "approval_revision_count": 0,
            "agentic_mode": False,
            "run_id": "golden",
            "thread_id": thread,
            "provider": "mock",
            "model": "mock",
            "model_calls": 0,
            "skip_ingest": False,
        },
    )


def test_golden_each_invoice(settings):
    for name, spec in GOLDEN.items():
        path = INV / name
        values = _run_file(settings, path)
        codes = {f["code"] for f in (values.get("validation") or {}).get("flags") or []}
        paid = values.get("outcome") == "PAY"
        assert paid is spec["pay"], f"{name} outcome={values.get('outcome')} flags={codes}"
        if spec.get("any"):
            assert codes & spec["any"], f"{name} missing {spec['any']} in {codes}"
        if spec.get("warn"):
            assert spec["warn"] <= codes or codes & spec["warn"], f"{name} expected warn {spec['warn']} got {codes}"


def test_batch_dedup_1011_and_revision(settings):
    pipeline = InvoicePipeline(settings, memory_checkpointer=True)
    files = [
        INV / "invoice_1004.json",
        INV / "invoice_1004_revised.json",
        INV / "invoice_1011.txt",
        INV / "invoice_1011.pdf",
    ]
    outcomes = []
    for path in files:
        values = pipeline.run(
            thread_id=path.name,
            payload={
                "raw_path": str(path),
                "events": [],
                "tool_trace": [],
                "agent_messages": [],
                "ingest_retry_count": 0,
                "validation_retry_count": 0,
                "approval_revision_count": 0,
                "agentic_mode": False,
                "run_id": "batch",
                "thread_id": path.name,
                "provider": "mock",
                "model": "mock",
                "model_calls": 0,
            },
        )
        outcomes.append((path.name, values.get("outcome"), {f["code"] for f in (values.get("validation") or {}).get("flags") or []}))
    by_name = {n: (o, f) for n, o, f in outcomes}
    assert by_name["invoice_1004.json"][0] == "PAY"
    assert by_name["invoice_1004_revised.json"][0] == "PAY"
    assert "REVISION" in by_name["invoice_1004_revised.json"][1]
    assert by_name["invoice_1011.txt"][0] == "PAY"
    assert by_name["invoice_1011.pdf"][0] == "DEDUP"


def test_collect_order_prefers_txt():
    files = collect_invoices(str(INV))
    names = [p.name for p in files]
    if "invoice_1011.pdf" in names and "invoice_1011.txt" in names:
        assert names.index("invoice_1011.txt") < names.index("invoice_1011.pdf")
