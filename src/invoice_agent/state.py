from __future__ import annotations

from typing import Annotated, Any, TypedDict

import operator


class GraphState(TypedDict, total=False):
    raw_path: str | None
    raw_text: str | None
    demo_mode: bool
    invoice_draft: dict[str, Any] | None
    invoice: dict[str, Any] | None
    validation: dict[str, Any] | None
    approval_draft: dict[str, Any] | None
    approval_critique: dict[str, Any] | None
    approval_final: dict[str, Any] | None
    human_review: dict[str, Any] | None
    payment: dict[str, Any] | None
    extra_flags: list[dict[str, Any]]
    events: Annotated[list[dict[str, Any]], operator.add]
    tool_trace: Annotated[list[dict[str, Any]], operator.add]
    agent_messages: Annotated[list[dict[str, Any]], operator.add]
    ingest_retry_count: int
    validation_retry_count: int
    approval_revision_count: int
    fatal_error: str | None
    agentic_mode: bool
    run_id: str
    thread_id: str
    outcome: str | None
    provider: str
    model: str
    model_calls: int
    skip_ingest: bool
