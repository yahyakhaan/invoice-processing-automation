from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from invoice_agent import storage
from invoice_agent.agents.approval import ApprovalAgent
from invoice_agent.agents.critic import ApprovalCriticAgent
from invoice_agent.agents.ingestion import DocumentIngestionAgent
from invoice_agent.agents.validation import ValidationAgent
from invoice_agent.config import Settings
from invoice_agent.observability.logging import event
from invoice_agent.orchestration.guards import apply_guard, payment_eligible, validation_tool_guard
from invoice_agent.orchestration.handoffs import handoff
from invoice_agent.schemas import Invoice, InvoiceDraft, ValidationReport
from invoice_agent.services.normalization import identity_flags, normalize_draft
from invoice_agent.state import GraphState
from invoice_agent.tools.payment import execute_payment


def _annotate_trace(agent_id: str, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "agent_id": agent_id} for item in trace]


class InvoicePipeline:
    def __init__(self, settings: Settings, *, memory_checkpointer: bool = False):
        self.settings = settings
        storage.ensure_database(settings)
        self.ingestion = DocumentIngestionAgent(settings)
        self.validation = ValidationAgent(settings)
        self.approval = ApprovalAgent(settings)
        self.critic = ApprovalCriticAgent(settings)
        if memory_checkpointer:
            self.checkpointer = MemorySaver()
        else:
            conn = sqlite3.connect(str(settings.checkpoint_db), check_same_thread=False)
            self.checkpointer = SqliteSaver(conn)
        self.graph = self._build().compile(checkpointer=self.checkpointer)

    def _build(self):
        graph = StateGraph(GraphState)
        graph.add_node("ingest", self.ingest_node)
        graph.add_node("normalize", self.normalize_node)
        graph.add_node("validate", self.validate_node)
        graph.add_node("approve", self.approve_node)
        graph.add_node("critique", self.critique_node)
        graph.add_node("human_review", self.human_review_node)
        graph.add_node("pay", self.pay_node)
        graph.add_node("reject", self.reject_node)
        graph.add_node("report", self.report_node)

        graph.add_conditional_edges(START, self.route_start, {"ingest": "ingest", "normalize": "normalize"})
        graph.add_conditional_edges(
            "ingest",
            self.route_after_ingest,
            {"ingest": "ingest", "normalize": "normalize", "reject": "reject"},
        )
        graph.add_edge("normalize", "validate")
        graph.add_conditional_edges(
            "validate",
            self.route_after_validate,
            {"validate": "validate", "approve": "approve", "reject": "reject"},
        )
        graph.add_edge("approve", "critique")
        graph.add_conditional_edges(
            "critique",
            self.route_after_critique,
            {"approve": "approve", "gate": "gate_router"},
        )
        graph.add_node("gate_router", self.gate_node)
        graph.add_conditional_edges(
            "gate_router",
            self.route_gate,
            {"pay": "pay", "reject": "reject", "human": "human_review"},
        )
        graph.add_conditional_edges(
            "human_review",
            self.route_after_human,
            {"pay": "pay", "reject": "reject"},
        )
        graph.add_edge("pay", "report")
        graph.add_edge("reject", "report")
        graph.add_edge("report", END)
        return graph

    def route_start(self, state: GraphState) -> str:
        if state.get("skip_ingest") and state.get("invoice"):
            return "normalize"
        if state.get("invoice") and state.get("skip_ingest"):
            return "normalize"
        return "ingest"

    def ingest_node(self, state: GraphState) -> dict[str, Any]:
        retry = int(state.get("ingest_retry_count") or 0)
        try:
            result = self.ingestion.invoke(
                {
                    "path": state.get("raw_path"),
                    "raw_text": state.get("raw_text"),
                    "retry_errors": state.get("fatal_error"),
                    "attempt": retry,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "fatal_error": "INGEST_ERROR",
                "ingest_retry_count": retry + 1,
                "events": [
                    event(
                        run_id=state["run_id"],
                        node="ingest",
                        agent_id="ingestion",
                        event_name="INGEST_ERROR",
                        level="ERROR",
                        data={"error": str(exc), "path": state.get("raw_path")},
                    )
                ],
            }
        draft = InvoiceDraft.model_validate(result.output)
        missing = []
        if not (draft.vendor_raw or "").strip():
            missing.append("vendor")
        if not draft.invoice_number:
            missing.append("invoice_number")
        if not draft.line_items:
            missing.append("line_items")
        events = [
            event(
                run_id=state["run_id"],
                invoice_id=draft.invoice_number,
                agent_id="ingestion",
                node="ingest",
                event_name="INGEST_COMPLETE",
                data={"missing": missing, "attempt": retry, "format": draft.source_format},
            ),
            handoff(
                run_id=state["run_id"],
                invoice_id=draft.invoice_number,
                source="ingestion",
                target="normalize" if retry >= 2 or not missing else "ingestion",
            ),
        ]
        return {
            "invoice_draft": draft.model_dump(),
            "raw_text": draft.raw_text or state.get("raw_text"),
            "ingest_retry_count": retry + (1 if missing else 0),
            "fatal_error": None if draft.line_items or draft.invoice_number else "INGEST_FAILED",
            "tool_trace": _annotate_trace("ingestion", result.tool_trace),
            "agent_messages": result.messages,
            "events": events,
            "model_calls": int(state.get("model_calls") or 0) + result.model_calls,
        }

    def route_after_ingest(self, state: GraphState) -> str:
        if state.get("fatal_error") in {"INGEST_ERROR", "INGEST_FAILED"} and int(state.get("ingest_retry_count") or 0) >= 2:
            return "reject"
        draft = state.get("invoice_draft") or {}
        missing = not draft.get("line_items") and not draft.get("invoice_number")
        if missing and int(state.get("ingest_retry_count") or 0) < 2:
            return "ingest"
        if missing and int(state.get("ingest_retry_count") or 0) >= 2:
            return "reject"
        if not draft.get("line_items") and int(state.get("ingest_retry_count") or 0) < 2:
            return "ingest"
        return "normalize"

    def normalize_node(self, state: GraphState) -> dict[str, Any]:
        if state.get("invoice") and state.get("skip_ingest") and not state.get("invoice_draft"):
            invoice = Invoice.model_validate(state["invoice"])
            flags = []
        else:
            draft = InvoiceDraft.model_validate(state["invoice_draft"])
            invoice = normalize_draft(draft)
            flags = [f.model_dump() for f in identity_flags(invoice, draft)]
        return {
            "invoice": invoice.model_dump(),
            "extra_flags": flags,
            "events": [
                event(
                    run_id=state["run_id"],
                    invoice_id=invoice.invoice_number,
                    node="normalize",
                    event_name="NORMALIZED",
                    data={"sku": [i.sku for i in invoice.line_items]},
                )
            ],
        }

    def validate_node(self, state: GraphState) -> dict[str, Any]:
        retry = int(state.get("validation_retry_count") or 0)
        result = self.validation.invoke(
            {
                "invoice": state["invoice"],
                "source_path": state.get("raw_path") or "",
                "raw_text": state.get("raw_text") or "",
                "extra_flags": state.get("extra_flags") or [],
                "skip_tools": state.get("skip_tools") if retry == 0 else [],
            }
        )
        report = ValidationReport.model_validate(result.output)
        missing = validation_tool_guard(report.tools_called)
        if missing and retry < 1:
            return {
                "validation_retry_count": retry + 1,
                "tool_trace": _annotate_trace("validation", result.tool_trace),
                "events": [
                    event(
                        run_id=state["run_id"],
                        invoice_id=(state.get("invoice") or {}).get("invoice_number"),
                        agent_id="validation",
                        event_name="VALIDATION_RETRY",
                        data={"missing": missing},
                    )
                ],
            }
        report = apply_guard(report)
        return {
            "validation": report.model_dump(),
            "validation_retry_count": retry,
            "tool_trace": _annotate_trace("validation", result.tool_trace),
            "events": [
                event(
                    run_id=state["run_id"],
                    invoice_id=(state.get("invoice") or {}).get("invoice_number"),
                    agent_id="validation",
                    node="validate",
                    event_name="VALIDATED",
                    data={"flags": [f.code for f in report.flags], "missing_tools": missing},
                ),
                handoff(
                    run_id=state["run_id"],
                    invoice_id=(state.get("invoice") or {}).get("invoice_number"),
                    source="validation",
                    target="approval",
                ),
            ],
            "model_calls": int(state.get("model_calls") or 0) + result.model_calls,
        }

    def route_after_validate(self, state: GraphState) -> str:
        report = state.get("validation")
        if report is None:
            return "validate"
        codes = {f["code"] for f in (report.get("flags") or [])}
        if "VALIDATION_INCOMPLETE" in codes and int(state.get("validation_retry_count") or 0) < 1:
            return "validate"
        if "DEDUP" in codes:
            return "reject"
        return "approve"

    def approve_node(self, state: GraphState) -> dict[str, Any]:
        result = self.approval.invoke(
            {
                "invoice": state["invoice"],
                "validation": state["validation"],
                "critique": state.get("approval_critique"),
            }
        )
        data = result.output
        fx_flags = (data.get("_fx") or {}).get("flags") or []
        usd = data.get("_usd")
        validation = dict(state["validation"])
        flags = list(validation.get("flags") or [])
        for flag in fx_flags:
            if isinstance(flag, dict) and not any(existing.get("code") == flag.get("code") for existing in flags):
                flags.append(flag)
        validation["flags"] = flags
        validation["usd_equivalent"] = usd
        key = "approval_final" if state.get("approval_critique") else "approval_draft"
        return {
            key: {k: v for k, v in data.items() if not k.startswith("_")},
            "validation": validation,
            "tool_trace": _annotate_trace("approval", result.tool_trace),
            "events": [
                event(
                    run_id=state["run_id"],
                    invoice_id=(state.get("invoice") or {}).get("invoice_number"),
                    agent_id="approval",
                    node="approve",
                    event_name="APPROVAL_DRAFT" if key == "approval_draft" else "APPROVAL_FINAL",
                    data={"decision": data.get("decision")},
                )
            ],
            "model_calls": int(state.get("model_calls") or 0) + result.model_calls,
        }