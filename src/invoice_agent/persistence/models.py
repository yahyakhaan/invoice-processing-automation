from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

APP_SCHEMA = "app"


class Base(DeclarativeBase):
    pass


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (CheckConstraint("stock >= 0", name="ck_inventory_stock_nonnegative"), {"schema": APP_SCHEMA})

    item: Mapped[str] = mapped_column(Text, primary_key=True)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))


class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = ({"schema": APP_SCHEMA},)

    canonical: Mapped[str] = mapped_column(Text, primary_key=True)
    aliases: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = ({"schema": APP_SCHEMA},)

    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    usd_per_unit: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED', 'RUNNING', 'PENDING_REVIEW', 'COMPLETED', 'FAILED')",
            name="ck_runs_status",
        ),
        Index("ix_runs_owner_created", "owner_id", "created_at"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class InvoiceRecord(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("thread_id", name="uq_invoices_thread_id"),
        Index("ix_invoices_run", "run_id"),
        Index("ix_invoices_number", "invoice_number"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{APP_SCHEMA}.runs.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    invoice_number: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(Text)
    source_format: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(32))
    invoice_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ValidationResult(Base):
    __tablename__ = "validation_results"
    __table_args__ = (UniqueConstraint("invoice_id", name="uq_validation_invoice"), {"schema": APP_SCHEMA})

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{APP_SCHEMA}.invoices.id", ondelete="CASCADE"), nullable=False
    )
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ApprovalDecisionRecord(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint("invoice_id", "decision_type", name="uq_approval_invoice_type"),
        CheckConstraint(
            "decision_type IN ('draft', 'critique', 'final')", name="ck_approval_decision_type"
        ),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{APP_SCHEMA}.invoices.id", ondelete="CASCADE"), nullable=False
    )
    decision_type: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "thread_id", "sequence", name="uq_run_event_sequence"),
        Index("ix_run_events_replay", "run_id", "thread_id", "sequence"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(f"{APP_SCHEMA}.runs.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ProcessedInvoice(Base):
    __tablename__ = "processed_invoices"
    __table_args__ = (
        Index("ix_processed_invoice_number", "invoice_number"),
        {"schema": APP_SCHEMA},
    )

    invoice_number: Mapped[str] = mapped_column(Text, primary_key=True)
    revision: Mapped[str] = mapped_column(Text, primary_key=True, default="")
    source_path: Mapped[str] = mapped_column(Text, primary_key=True, default="")
    canonical_hash: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(String(32))
    run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class HumanReview(Base):
    __tablename__ = "human_reviews"
    __table_args__ = ({"schema": APP_SCHEMA},)

    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = ({"schema": APP_SCHEMA},)

    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    invoice_number: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    vendor: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
