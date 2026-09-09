from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

RunStatus = Literal["CREATED", "RUNNING", "PENDING_REVIEW", "COMPLETED", "FAILED"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    database_backend: Literal["postgres", "sqlite"]
    provider: str
    llm_enabled: bool
    llm_daily_limit: int
    llm_runs_remaining: int


class SampleInvoice(BaseModel):
    id: str
    name: str
    format: str
    size_bytes: int | None = None
    is_demo: bool = False
    content_url: str | None = None


class RunCreated(BaseModel):
    run_id: UUID
    thread_id: UUID
    status: RunStatus
    detail_url: str
    events_url: str
    stream_url: str
    review_url: str


class RunSummary(BaseModel):
    run_id: UUID
    thread_id: UUID
    status: RunStatus
    outcome: str | None = None
    invoice_id: str | None = None
    source: str | None = None
    provider: str
    model: str
    created_at: datetime
    updated_at: datetime


class RunDetail(RunSummary):
    invoice: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    approval_draft: dict[str, Any] | None = None
    approval_critique: dict[str, Any] | None = None
    approval_final: dict[str, Any] | None = None
    human_review: dict[str, Any] | None = None
    payment: dict[str, Any] | None = None
    pending_interrupt: dict[str, Any] | None = None
    agentic_mode: bool = False
    model_calls: int = 0
    tool_calls: int = 0
    error: str | None = None


class RunList(BaseModel):
    items: list[RunSummary]


class PublicEvent(BaseModel):
    event_id: UUID
    run_id: UUID
    thread_id: UUID
    sequence: int = Field(ge=1)
    timestamp: datetime
    stage: str | None = None
    event_type: str
    status: RunStatus
    level: str = "INFO"
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class EventList(BaseModel):
    items: list[PublicEvent]


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    actor: str = Field(default="VP", min_length=1, max_length=120)
    rationale: str = Field(default="", max_length=2000)


class ReviewAccepted(BaseModel):
    run_id: UUID
    status: Literal["RUNNING"] = "RUNNING"
    stream_url: str
