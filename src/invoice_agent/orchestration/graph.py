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
from invoice_agent.observability.console import progress
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
        progress(
            "stage: ingest",
            detail=f"attempt={retry + 1} path={Path(state.get('raw_path') or '').name or 'inline'}",
        )
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
        progress(
            "stage: ingest complete",
            detail=f"invoice={draft.invoice_number or '?'} items={len(draft.line_items)} missing={missing or 'none'}",
        )
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
        progress("stage: normalize")
        if state.get("invoice") and state.get("skip_ingest") and not state.get("invoice_draft"):
            invoice = Invoice.model_validate(state["invoice"])
            flags = []
        else:
            draft = InvoiceDraft.model_validate(state["invoice_draft"])
            invoice = normalize_draft(draft)
            flags = [f.model_dump() for f in identity_flags(invoice, draft)]
        progress(
            "stage: normalize complete",
            detail=f"vendor={invoice.vendor_canonical} skus={[i.sku for i in invoice.line_items]}",
        )
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
        progress("stage: validate", detail=f"attempt={retry + 1}")
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
        progress(
            "stage: validate complete",
            detail=f"flags={[f.code for f in report.flags] or ['none']}",
        )
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
        progress("stage: approve")
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
        progress("stage: approve complete", detail=f"decision={data.get('decision')}")
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

    def critique_node(self, state: GraphState) -> dict[str, Any]:
        draft = state.get("approval_final") or state.get("approval_draft")
        progress("stage: critique")
        result = self.critic.invoke(
            {
                "approval_draft": draft,
                "validation": state["validation"],
            }
        )
        bump = 1 if result.output.get("verdict") == "revise" else 0
        progress("stage: critique complete", detail=f"verdict={result.output.get('verdict')}")
        return {
            "approval_critique": result.output,
            "approval_revision_count": int(state.get("approval_revision_count") or 0) + bump,
            "tool_trace": _annotate_trace("critic", result.tool_trace),
            "events": [
                event(
                    run_id=state["run_id"],
                    invoice_id=(state.get("invoice") or {}).get("invoice_number"),
                    agent_id="critic",
                    node="critique",
                    event_name="CRITIQUE",
                    data={"verdict": result.output.get("verdict")},
                ),
                handoff(
                    run_id=state["run_id"],
                    invoice_id=(state.get("invoice") or {}).get("invoice_number"),
                    source="critic",
                    target="approval" if result.output.get("verdict") == "revise" else "gate",
                ),
            ],
            "model_calls": int(state.get("model_calls") or 0) + result.model_calls,
        }

    def route_after_critique(self, state: GraphState) -> str:
        critique = state.get("approval_critique") or {}
        if critique.get("verdict") == "revise" and int(state.get("approval_revision_count") or 0) <= 1:
            return "approve"
        return "gate"

    def gate_node(self, state: GraphState) -> dict[str, Any]:
        revision = int(state.get("approval_revision_count") or 0)
        critique = state.get("approval_critique") or {}
        bump = 1 if critique.get("verdict") == "revise" and revision < 1 else 0
        final = state.get("approval_final") or state.get("approval_draft")
        progress("stage: payment gate", detail=f"decision={(final or {}).get('decision')}")
        return {
            "approval_final": final,
            "approval_revision_count": revision + bump,
            "events": [
                event(
                    run_id=state["run_id"],
                    invoice_id=(state.get("invoice") or {}).get("invoice_number"),
                    node="gate",
                    event_name="PAYMENT_GATE",
                    data={"decision": (final or {}).get("decision")},
                )
            ],
        }

    def route_gate(self, state: GraphState) -> str:
        report = ValidationReport.model_validate(state["validation"])
        final = state.get("approval_final") or {}
        human = state.get("human_review")
        usd = report.usd_equivalent or 0
        high = usd > self.settings.high_value_usd
        fraud = "FRAUD_LANGUAGE" in report.codes()
        action = payment_eligible(
            blocking=report.has_blocking,
            final_decision=final.get("decision"),
            human_decision=(human or {}).get("decision"),
            high_value=high,
            fraud=fraud,
        )
        progress(
            "stage: gate route",
            detail=f"action={action} usd={usd} high_value={high} fraud={fraud}",
        )
        if action == "pay":
            return "pay"
        if action == "pending":
            return "human"
        return "reject"

    def human_review_node(self, state: GraphState) -> dict[str, Any]:
        invoice = state.get("invoice") or {}
        progress(
            "stage: VP interrupt",
            detail=f"invoice={invoice.get('invoice_number')} total={invoice.get('total')}",
        )
        payload = interrupt(
            {
                "type": "vp_review",
                "invoice_number": invoice.get("invoice_number"),
                "vendor": invoice.get("vendor_canonical"),
                "total": invoice.get("total"),
                "currency": invoice.get("currency"),
                "usd_equivalent": (state.get("validation") or {}).get("usd_equivalent"),
                "rationale": (state.get("approval_final") or {}).get("rationale"),
                "thread_id": state.get("thread_id"),
            }
        )
        decision = payload.get("decision") if isinstance(payload, dict) else payload
        actor = payload.get("actor", "VP") if isinstance(payload, dict) else "VP"
        rationale = payload.get("rationale", "") if isinstance(payload, dict) else ""
        conn = storage.connect(Path(self.settings.inventory_db))
        try:
            storage.record_human_decision(
                conn,
                thread_id=state["thread_id"],
                decision=str(decision),
                actor=str(actor),
                rationale=str(rationale),
            )
        finally:
            conn.close()
        return {
            "human_review": {
                "decision": decision,
                "actor": actor,
                "rationale": rationale,
            },
            "events": [
                event(
                    run_id=state["run_id"],
                    invoice_id=invoice.get("invoice_number"),
                    node="human_review",
                    event_name="VP_DECISION",
                    data={"decision": decision, "actor": actor},
                )
            ],
        }

    def route_after_human(self, state: GraphState) -> str:
        report = ValidationReport.model_validate(state["validation"])
        if report.has_blocking:
            return "reject"
        if (state.get("human_review") or {}).get("decision") == "approve":
            return "pay"
        return "reject"

    def pay_node(self, state: GraphState) -> dict[str, Any]:
        invoice = Invoice.model_validate(state["invoice"])
        amount = invoice.total if invoice.total is not None else 0.0
        progress("stage: pay", detail=f"{amount} → {invoice.vendor_canonical}")
        paid = execute_payment(
            vendor=invoice.vendor_canonical,
            amount=amount,
            invoice_number=invoice.invoice_number,
            revision=invoice.revision,
            settings=self.settings,
        )
        outcome = "PAY" if paid["status"] == "success" else "PAYMENT_FAILED"
        return {
            "payment": paid,
            "outcome": outcome,
            "events": [
                event(
                    run_id=state["run_id"],
                    invoice_id=invoice.invoice_number,
                    node="pay",
                    event_name=outcome,
                    data={"amount": amount, "vendor": invoice.vendor_canonical},
                )
            ],
        }

    def reject_node(self, state: GraphState) -> dict[str, Any]:
        report = ValidationReport.model_validate(state["validation"]) if state.get("validation") else None
        codes = report.codes() if report else set()
        if "DEDUP" in codes:
            outcome = "DEDUP"
        elif state.get("fatal_error") == "INGEST_FAILED":
            outcome = "INGEST_FAILED"
        else:
            outcome = "REJECT"
        progress("stage: reject", detail=f"outcome={outcome} flags={sorted(codes)}")
        invoice_id = (state.get("invoice") or state.get("invoice_draft") or {}).get("invoice_number")
        return {
            "outcome": outcome,
            "events": [
                event(
                    run_id=state["run_id"],
                    invoice_id=invoice_id,
                    node="reject",
                    event_name=outcome,
                    data={"flags": sorted(codes)},
                )
            ],
        }

    def report_node(self, state: GraphState) -> dict[str, Any]:
        invoice = state.get("invoice") or {}
        validation = state.get("validation") or {}
        outcome = state.get("outcome") or "UNKNOWN"
        progress("stage: report", detail=f"outcome={outcome}")
        conn = storage.connect(Path(self.settings.inventory_db))
        try:
            if invoice.get("invoice_number"):
                storage.record_processed(
                    conn,
                    invoice_number=invoice["invoice_number"],
                    revision=invoice.get("revision"),
                    source_path=state.get("raw_path") or "",
                    canonical_hash=validation.get("canonical_hash"),
                    outcome=outcome,
                    run_id=state["run_id"],
                )
        finally:
            conn.close()
        return {
            "events": [
                event(
                    run_id=state["run_id"],
                    invoice_id=invoice.get("invoice_number"),
                    node="report",
                    event_name="COMPLETE",
                    data={"outcome": outcome},
                )
            ]
        }

    def run(self, *, thread_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        config = {"configurable": {"thread_id": thread_id}}
        self.graph.invoke(payload, config)
        return self.snapshot(thread_id)

    def resume(self, *, thread_id: str, decision: str, actor: str, rationale: str) -> dict[str, Any]:
        config = {"configurable": {"thread_id": thread_id}}
        snap = self.snapshot(thread_id)
        if snap.get("outcome") in {"PAY", "REJECT", "DEDUP", "INGEST_FAILED", "PAYMENT_FAILED"}:
            return snap
        self.graph.invoke(
            Command(resume={"decision": decision, "actor": actor, "rationale": rationale}),
            config,
        )
        return self.snapshot(thread_id)

    def snapshot(self, thread_id: str) -> dict[str, Any]:
        config = {"configurable": {"thread_id": thread_id}}
        snap = self.graph.get_state(config)
        values = dict(snap.values)
        pending = bool(snap.next) and "human_review" in (snap.next or ())
        if pending or (not values.get("outcome") and snap.tasks):
            interrupts = []
            for task in snap.tasks or []:
                interrupts.extend([i.value for i in (task.interrupts or [])])
            if interrupts or pending:
                values["outcome"] = "PENDING_VP_REVIEW"
                values["pending_interrupt"] = interrupts[0] if interrupts else {"type": "vp_review"}
        return values
