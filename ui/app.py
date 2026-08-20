from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from invoice_agent.config import get_settings
from invoice_agent.runner import resume_review

st.set_page_config(page_title="Invoice Agent Inspector", layout="wide")
st.title("Invoice Agent Inspector")
st.caption("Local run viewer and VP review queue. Not a production identity system.")

output_root = ROOT / "outputs"
runs = sorted([p for p in output_root.glob("*") if p.is_dir()], reverse=True) if output_root.exists() else []
if not runs:
    st.info("No runs yet. Execute `python main.py --invoice_path=data/invoices` first.")
    st.stop()

run = st.selectbox("Run", runs, format_func=lambda p: p.name)
results = sorted(run.glob("*/result.json"))
st.subheader("Corpus outcomes")
rows = []
for path in results:
    data = json.loads(path.read_text())
    rows.append(
        {
            "invoice": data.get("invoice_id"),
            "outcome": data.get("outcome"),
            "provider": data.get("provider"),
            "flags": ", ".join(sorted({f["code"] for f in (data.get("validation") or {}).get("flags") or []})),
            "path": str(path.parent.name),
        }
    )
st.dataframe(rows, use_container_width=True)

choice = st.selectbox("Inspect invoice", results, format_func=lambda p: p.parent.name)
data = json.loads(choice.read_text())
c1, c2, c3 = st.columns(3)
c1.metric("Outcome", data.get("outcome"))
c2.metric("Agentic", str(data.get("agentic_mode")))
c3.metric("Tool calls", data.get("tool_calls"))
if not data.get("agentic_mode"):
    st.warning("DETERMINISTIC FALLBACK — NO LLM")

st.json(
    {
        "invoice": data.get("invoice"),
        "validation": data.get("validation"),
        "approval_draft": data.get("approval_draft"),
        "approval_critique": data.get("approval_critique"),
        "approval_final": data.get("approval_final"),
        "human_review": data.get("human_review"),
        "payment": data.get("payment"),
    }
)
st.subheader("Timeline")
for ev in data.get("events") or []:
    st.write(f"{ev.get('ts')} · {ev.get('agent_id') or ev.get('node')} · {ev.get('event')}")

pending = [r for r in rows if r["outcome"] == "PENDING_VP_REVIEW"]
st.subheader("VP review queue")
if not pending:
    st.write("No pending high-value invoices in this run.")
else:
    selected = st.selectbox("Pending thread", pending, format_func=lambda r: r["invoice"])
    thread = json.loads((run / selected["path"] / "result.json").read_text()).get("thread_id")
    actor = st.text_input("Actor", "VP")
    reason = st.text_area("Rationale", "Reviewed in Streamlit")
    col_a, col_b = st.columns(2)
    if col_a.button("Approve") and thread:
        resume_review(get_settings(), thread, "approve", actor, reason, ROOT / "outputs")
        st.success("Approved and resumed.")
        st.rerun()
    if col_b.button("Reject") and thread:
        resume_review(get_settings(), thread, "reject", actor, reason, ROOT / "outputs")
        st.success("Rejected and resumed.")
        st.rerun()
