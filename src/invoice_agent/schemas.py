from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Decision = Literal["approve", "reject", "escalate"]
CritiqueVerdict = Literal["accept", "revise"]
FlagSeverity = Literal["blocking", "warning", "info"]

BLOCKING_FLAGS = {
    "MISSING_REQUIRED_FIELD",
    "DATA_INTEGRITY",
    "UNKNOWN_ITEM",
    "ZERO_STOCK",
    "OVER_STOCK",
    "TOTAL_MISMATCH",
    "DUPLICATE",
    "VALIDATION_INCOMPLETE",
    "INGEST_FAILED",
    "UNSUPPORTED_EXTRACTION",
    "PAYMENT_FAILED",
}

MANDATORY_VALIDATION_TOOLS = {
    "check_required_fields",
    "check_integrity",
    "lookup_inventory",
    "check_stock",
    "reconcile_totals",
    "check_duplicate",
    "scan_fraud_signals",
}


class LineItem(BaseModel):
    sku: str
    raw_name: str
    quantity: float
    unit_price: float | None = None
    amount: float | None = None
    note: str | None = None


class InvoiceDraft(BaseModel):
    invoice_number: str | None = None
    revision: str | None = None
    vendor_raw: str | None = None
    currency: str = "USD"
    invoice_date: date | None = None
    due_date: date | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax: float | None = None
    shipping: float | None = None
    total: float | None = None
    payment_terms: str | None = None
    source_format: str = "unknown"
    fraud_signals: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    unresolved_fields: list[str] = Field(default_factory=list)
    evidence_spans: list[str] = Field(default_factory=list)
    raw_text: str | None = None


class Invoice(BaseModel):
    invoice_number: str
    revision: str | None = None
    vendor_raw: str
    vendor_canonical: str
    currency: str = "USD"
    invoice_date: date | None = None
    due_date: date | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax: float | None = None
    shipping: float | None = None
    total: float | None = None
    payment_terms: str | None = None
    source_format: str = "unknown"
    fraud_signals: list[str] = Field(default_factory=list)
    vendor_identity_warning: bool = False


class ValidationFlag(BaseModel):
    code: str
    severity: FlagSeverity
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class StockCheck(BaseModel):
    sku: str
    requested: float
    stock: int | None
    found: bool
    ok: bool


class ValidationReport(BaseModel):
    flags: list[ValidationFlag] = Field(default_factory=list)
    stock_checks: list[StockCheck] = Field(default_factory=list)
    aggregates: dict[str, float] = Field(default_factory=dict)
    usd_equivalent: float | None = None
    canonical_hash: str | None = None
    summary: str = ""
    tools_called: list[str] = Field(default_factory=list)

    @property
    def blocking(self) -> list[ValidationFlag]:
        return [f for f in self.flags if f.severity == "blocking" or f.code in BLOCKING_FLAGS]

    @property
    def has_blocking(self) -> bool:
        return bool(self.blocking)

    def codes(self) -> set[str]:
        return {f.code for f in self.flags}


class ValidationSynthesis(BaseModel):
    summary: str
    extra_flags: list[ValidationFlag] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    decision: Decision
    rationale: str
    risk_score: float = 0.0
    rule_hits: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    revised_from: str | None = None


class ApprovalCritique(BaseModel):
    verdict: CritiqueVerdict
    issues: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    rationale: str = ""


class HumanDecision(BaseModel):
    decision: Literal["approve", "reject"]
    actor: str
    rationale: str
    decided_at: datetime


class PaymentResult(BaseModel):
    status: str
    vendor: str
    amount: float
    idempotency_key: str
    response: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 1


class AgentResult(BaseModel):
    agent_id: str
    output: dict[str, Any] = Field(default_factory=dict)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    model_calls: int = 0
    latency_ms: float = 0.0


class LogEvent(BaseModel):
    ts: str
    run_id: str
    invoice_id: str | None = None
    agent_id: str | None = None
    node: str | None = None
    tool: str | None = None
    attempt: int | None = None
    level: str = "INFO"
    event: str
    data: dict[str, Any] = Field(default_factory=dict)
