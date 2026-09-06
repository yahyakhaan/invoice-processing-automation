"""Create the PostgreSQL application foundation.

Revision ID: 0001_postgres_foundation
Revises: None
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_postgres_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "app"


def upgrade() -> None:
    op.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))

    op.create_table(
        "inventory",
        sa.Column("item", sa.Text(), primary_key=True),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 2)),
        sa.CheckConstraint("stock >= 0", name="ck_inventory_stock_nonnegative"),
        schema=SCHEMA,
    )
    op.create_table(
        "vendors",
        sa.Column("canonical", sa.Text(), primary_key=True),
        sa.Column("aliases", postgresql.JSONB(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "fx_rates",
        sa.Column("currency", sa.String(3), primary_key=True),
        sa.Column("usd_per_unit", sa.Numeric(18, 8), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('CREATED', 'RUNNING', 'PENDING_REVIEW', 'COMPLETED', 'FAILED')",
            name="ck_runs_status",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_runs_owner_created", "runs", ["owner_id", "created_at"], schema=SCHEMA)

    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_number", sa.Text()),
        sa.Column("revision", sa.Text()),
        sa.Column("source_path", sa.Text()),
        sa.Column("source_format", sa.String(32)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32)),
        sa.Column("invoice_data", postgresql.JSONB()),
        sa.Column("result_data", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("thread_id", name="uq_invoices_thread_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_invoices_run", "invoices", ["run_id"], schema=SCHEMA)
    op.create_index("ix_invoices_number", "invoices", ["invoice_number"], schema=SCHEMA)

    op.create_table(
        "validation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("invoice_id", name="uq_validation_invoice"),
        schema=SCHEMA,
    )
    op.create_table(
        "approval_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision_type", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "decision_type IN ('draft', 'critique', 'final')",
            name="ck_approval_decision_type",
        ),
        sa.UniqueConstraint("invoice_id", "decision_type", name="uq_approval_invoice_type"),
        schema=SCHEMA,
    )
    op.create_table(
        "run_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.String(32)),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", "thread_id", "sequence", name="uq_run_event_sequence"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_run_events_replay", "run_events", ["run_id", "thread_id", "sequence"], schema=SCHEMA
    )
    op.create_table(
        "processed_invoices",
        sa.Column("invoice_number", sa.Text(), primary_key=True),
        sa.Column("revision", sa.Text(), primary_key=True, server_default=""),
        sa.Column("source_path", sa.Text(), primary_key=True, server_default=""),
        sa.Column("canonical_hash", sa.Text()),
        sa.Column("outcome", sa.String(32)),
        sa.Column("run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_processed_invoice_number", "processed_invoices", ["invoice_number"], schema=SCHEMA
    )
    op.create_table(
        "human_reviews",
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("actor", sa.Text()),
        sa.Column("rationale", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "payment_attempts",
        sa.Column("idempotency_key", sa.Text(), primary_key=True),
        sa.Column("invoice_number", sa.Text()),
        sa.Column("amount", sa.Numeric(18, 2)),
        sa.Column("vendor", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO app.inventory (item, stock, unit_cost) VALUES
                ('WidgetA', 15, 250),
                ('WidgetB', 10, 500),
                ('GadgetX', 5, 750),
                ('FakeItem', 0, 1000)
            ON CONFLICT (item) DO UPDATE
            SET stock = EXCLUDED.stock, unit_cost = EXCLUDED.unit_cost
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO app.vendors (canonical, aliases)
            VALUES (
                'QuickShip',
                '["QuickShip Distributers", "QuickShip Distributors", "FastShip Ltd.", "FastShip"]'::jsonb
            )
            ON CONFLICT (canonical) DO UPDATE SET aliases = EXCLUDED.aliases
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO app.fx_rates (currency, usd_per_unit) VALUES
                ('USD', 1.0),
                ('EUR', 1.08)
            ON CONFLICT (currency) DO UPDATE SET usd_per_unit = EXCLUDED.usd_per_unit
            """
        )
    )


def downgrade() -> None:
    op.drop_table("payment_attempts", schema=SCHEMA)
    op.drop_table("human_reviews", schema=SCHEMA)
    op.drop_index("ix_processed_invoice_number", table_name="processed_invoices", schema=SCHEMA)
    op.drop_table("processed_invoices", schema=SCHEMA)
    op.drop_index("ix_run_events_replay", table_name="run_events", schema=SCHEMA)
    op.drop_table("run_events", schema=SCHEMA)
    op.drop_table("approval_decisions", schema=SCHEMA)
    op.drop_table("validation_results", schema=SCHEMA)
    op.drop_index("ix_invoices_number", table_name="invoices", schema=SCHEMA)
    op.drop_index("ix_invoices_run", table_name="invoices", schema=SCHEMA)
    op.drop_table("invoices", schema=SCHEMA)
    op.drop_index("ix_runs_owner_created", table_name="runs", schema=SCHEMA)
    op.drop_table("runs", schema=SCHEMA)
    op.drop_table("fx_rates", schema=SCHEMA)
    op.drop_table("vendors", schema=SCHEMA)
    op.drop_table("inventory", schema=SCHEMA)
    op.execute(sa.text(f"DROP SCHEMA IF EXISTS {SCHEMA}"))

