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