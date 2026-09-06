from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeAlias
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Connection, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.orm import Session

from invoice_agent.config import ROOT, Settings, get_settings
from invoice_agent.persistence.database import get_engine, run_migrations
from invoice_agent.persistence.models import (
    ApprovalDecisionRecord,
    FxRate,
    HumanReview,
    Inventory,
    InvoiceRecord,
    PaymentAttempt,
    ProcessedInvoice,
    Run,
    RunEvent,
    ValidationResult,
    Vendor,
)

SCHEMA_SQL = (ROOT / "data" / "seed_inventory.sql").read_text(encoding="utf-8")
StorageConnection: TypeAlias = sqlite3.Connection | Connection


def _as_uuid(value: str | UUID) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value)
    except ValueError:
        # Legacy CLI and checkpoint identifiers were human-readable strings.
        # A namespace UUID keeps migrated references deterministic.
        return uuid5(NAMESPACE_URL, value)


def _is_postgres(conn: StorageConnection) -> bool:
    return isinstance(conn, Connection)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def connect(target: Settings | Path) -> StorageConnection:
    if isinstance(target, Settings):
        if target.database_url:
            return get_engine(target.database_url).connect()
        db_path = Path(target.inventory_db)
    else:
        db_path = Path(target)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_database(settings: Settings | None = None) -> Path | str:
    settings = settings or get_settings()
    if settings.database_url:
        run_migrations(settings.database_url)
        return settings.database_url

    path = Path(settings.inventory_db)
    conn = connect(path)
    try:
        assert isinstance(conn, sqlite3.Connection)
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    Path(settings.checkpoint_db).parent.mkdir(parents=True, exist_ok=True)
    return path


def lookup_item(conn: StorageConnection, sku: str) -> dict[str, Any] | None:
    if _is_postgres(conn):
        row = conn.execute(
            text("SELECT item, stock, unit_cost FROM app.inventory WHERE item = :sku"),
            {"sku": sku},
        ).mappings().first()
        result = dict(row) if row else None
        if result and result.get("unit_cost") is not None:
            result["unit_cost"] = float(result["unit_cost"])
        return result
    row = conn.execute("SELECT item, stock, unit_cost FROM inventory WHERE item = ?", (sku,)).fetchone()
    return dict(row) if row else None


def fx_rate(conn: StorageConnection, currency: str, default_eur: float = 1.08) -> float:
    currency = currency.upper()
    if _is_postgres(conn):
        row = conn.execute(
            text("SELECT usd_per_unit FROM app.fx_rates WHERE currency = :currency"),
            {"currency": currency},
        ).mappings().first()
    else:
        row = conn.execute(
            "SELECT usd_per_unit FROM fx_rates WHERE currency = ?", (currency,)
        ).fetchone()
    if row:
        return float(row["usd_per_unit"])
    if currency == "EUR":
        return default_eur
    return 1.0


def find_processed(conn: StorageConnection, invoice_number: str) -> list[dict[str, Any]]:
    if _is_postgres(conn):
        rows = conn.execute(
            text(
                "SELECT * FROM app.processed_invoices "
                "WHERE invoice_number = :invoice_number ORDER BY created_at"
            ),
            {"invoice_number": invoice_number},
        ).mappings().all()
    else:
        rows = conn.execute(
            "SELECT * FROM processed_invoices WHERE invoice_number = ? ORDER BY created_at",
            (invoice_number,),
        ).fetchall()
    return [dict(row) for row in rows]


def clear_processed_ledger(settings: Settings | None = None) -> dict[str, int]:
    """Wipe processed invoice history so corpus demos are not short-circuited by DEDUP."""
    settings = settings or get_settings()
    ensure_database(settings)
    conn = connect(settings)
    try:
        if _is_postgres(conn):
            processed = conn.execute(text("SELECT COUNT(*) FROM app.processed_invoices")).scalar_one()
            payments = conn.execute(text("SELECT COUNT(*) FROM app.payment_attempts")).scalar_one()
            conn.execute(text("DELETE FROM app.processed_invoices"))
            conn.execute(text("DELETE FROM app.payment_attempts"))
        else:
            processed = conn.execute("SELECT COUNT(*) FROM processed_invoices").fetchone()[0]
            payments = conn.execute("SELECT COUNT(*) FROM payment_attempts").fetchone()[0]
            conn.execute("DELETE FROM processed_invoices")
            conn.execute("DELETE FROM payment_attempts")
        conn.commit()
        return {"processed_invoices": int(processed), "payment_attempts": int(payments)}
    finally:
        conn.close()


def record_processed(
    conn: StorageConnection,
    *,
    invoice_number: str,
    revision: str | None,
    source_path: str | None,
    canonical_hash: str | None,
    outcome: str,
    run_id: str,
) -> None:
    now = datetime.now(UTC)
    if _is_postgres(conn):
        statement = postgres_insert(ProcessedInvoice).values(
            invoice_number=invoice_number,
            revision=revision or "",
            source_path=source_path or "",
            canonical_hash=canonical_hash,
            outcome=outcome,
            run_id=_as_uuid(run_id),
            created_at=now,
        )
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=["invoice_number", "revision", "source_path"],
                set_={
                    "canonical_hash": statement.excluded.canonical_hash,
                    "outcome": statement.excluded.outcome,
                    "run_id": statement.excluded.run_id,
                    "created_at": statement.excluded.created_at,
                },
            )
        )
    else:
        conn.execute(
            """
            INSERT OR REPLACE INTO processed_invoices
            (invoice_number, revision, source_path, canonical_hash, outcome, run_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice_number,
                revision or "",
                source_path or "",
                canonical_hash,
                outcome,
                run_id,
                now.isoformat(),
            ),
        )
    conn.commit()


def record_human_decision(
    conn: StorageConnection,
    *,
    thread_id: str,
    decision: str,
    actor: str,
    rationale: str,
) -> None:
    now = datetime.now(UTC)
    if _is_postgres(conn):
        statement = postgres_insert(HumanReview).values(
            thread_id=_as_uuid(thread_id),
            decision=decision,
            actor=actor,
            rationale=rationale,
            decided_at=now,
        )
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=["thread_id"],
                set_={
                    "decision": statement.excluded.decision,
                    "actor": statement.excluded.actor,
                    "rationale": statement.excluded.rationale,
                    "decided_at": statement.excluded.decided_at,
                },
            )
        )
    else:
        conn.execute(
            """
            INSERT OR REPLACE INTO human_decisions
            (thread_id, decision, actor, rationale, decided_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (thread_id, decision, actor, rationale, now.isoformat()),
        )
    conn.commit()


def get_payment_attempt(conn: StorageConnection, key: str) -> dict[str, Any] | None:
    if _is_postgres(conn):
        row = conn.execute(
            text("SELECT * FROM app.payment_attempts WHERE idempotency_key = :key"),
            {"key": key},
        ).mappings().first()
    else:
        row = conn.execute(
            "SELECT * FROM payment_attempts WHERE idempotency_key = ?", (key,)
        ).fetchone()
    return dict(row) if row else None


def record_payment_attempt(
    conn: StorageConnection,
    *,
    key: str,
    invoice_number: str,
    amount: float,
    vendor: str,
    status: str,
    response: dict[str, Any],
) -> None:
    now = datetime.now(UTC)
    if _is_postgres(conn):
        statement = postgres_insert(PaymentAttempt).values(
            idempotency_key=key,
            invoice_number=invoice_number,
            amount=amount,
            vendor=vendor,
            status=status,
            response=response,
            created_at=now,
        )
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=["idempotency_key"],
                set_={
                    "status": statement.excluded.status,
                    "response": statement.excluded.response,
                    "created_at": statement.excluded.created_at,
                },
            )
        )
    else:
        conn.execute(
            """
            INSERT OR REPLACE INTO payment_attempts
            (idempotency_key, invoice_number, amount, vendor, status, response, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (key, invoice_number, amount, vendor, status, json.dumps(response), now.isoformat()),
        )
    conn.commit()


def _run_status(outcome: str | None) -> str:
    if outcome == "PENDING_VP_REVIEW" or outcome is None:
        return "PENDING_REVIEW" if outcome else "RUNNING"
    if outcome in {"INGEST_FAILED", "PAYMENT_FAILED"}:
        return "FAILED"
    return "COMPLETED"


def record_run_snapshot(
    settings: Settings,
    payload: dict[str, Any],
    *,
    persist_events: bool = True,
) -> None:
    """Persist the product-facing snapshot without exposing checkpoint internals."""
    if not settings.database_url:
        return

    run_id = _as_uuid(payload["run_id"])
    thread_id = _as_uuid(payload["thread_id"])
    status = _run_status(payload.get("outcome"))
    invoice = payload.get("invoice") or {}
    engine = get_engine(settings.database_url)

    with Session(engine) as session, session.begin():
        run_statement = postgres_insert(Run).values(
            id=run_id,
            owner_id=settings.owner_id,
            status=status,
            provider=payload.get("provider") or settings.provider,
            model=payload.get("model") or settings.model_name,
        )
        session.execute(
            run_statement.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "status": run_statement.excluded.status,
                    "provider": run_statement.excluded.provider,
                    "model": run_statement.excluded.model,
                    "updated_at": func.now(),
                },
            )
        )

        invoice_statement = postgres_insert(InvoiceRecord).values(
            id=uuid5(NAMESPACE_URL, f"invoice:{thread_id}"),
            run_id=run_id,
            thread_id=thread_id,
            invoice_number=payload.get("invoice_id") or invoice.get("invoice_number"),
            revision=invoice.get("revision"),
            source_path=payload.get("source"),
            source_format=invoice.get("source_format"),
            status=status,
            outcome=payload.get("outcome"),
            invoice_data=_json_safe(invoice) if invoice else None,
            result_data=_json_safe(payload),
        )
        invoice_id = session.execute(
            invoice_statement.on_conflict_do_update(
                index_elements=["thread_id"],
                set_={
                    "status": invoice_statement.excluded.status,
                    "outcome": invoice_statement.excluded.outcome,
                    "invoice_data": invoice_statement.excluded.invoice_data,
                    "result_data": invoice_statement.excluded.result_data,
                    "updated_at": func.now(),
                },
            ).returning(InvoiceRecord.id)
        ).scalar_one()

        validation = payload.get("validation")
        if validation is not None:
            validation_statement = postgres_insert(ValidationResult).values(
                id=uuid5(NAMESPACE_URL, f"validation:{invoice_id}"),
                invoice_id=invoice_id,
                report=_json_safe(validation),
            )
            session.execute(
                validation_statement.on_conflict_do_update(
                    index_elements=["invoice_id"],
                    set_={"report": validation_statement.excluded.report, "updated_at": func.now()},
                )
            )

        decisions = {
            "draft": payload.get("approval_draft"),
            "critique": payload.get("approval_critique"),
            "final": payload.get("approval_final"),
        }
        for decision_type, decision_payload in decisions.items():
            if decision_payload is None:
                continue
            decision_statement = postgres_insert(ApprovalDecisionRecord).values(
                id=uuid5(NAMESPACE_URL, f"approval:{invoice_id}:{decision_type}"),
                invoice_id=invoice_id,
                decision_type=decision_type,
                payload=_json_safe(decision_payload),
            )
            session.execute(
                decision_statement.on_conflict_do_update(
                    index_elements=["invoice_id", "decision_type"],
                    set_={"payload": decision_statement.excluded.payload, "updated_at": func.now()},
                )
            )

        if persist_events:
            for sequence, item in enumerate(payload.get("events") or [], start=1):
                raw_timestamp = item.get("ts")
                try:
                    occurred_at = (
                        datetime.fromisoformat(raw_timestamp) if raw_timestamp else datetime.now(UTC)
                    )
                except ValueError:
                    occurred_at = datetime.now(UTC)
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=UTC)
                event_statement = postgres_insert(RunEvent).values(
                    id=uuid5(NAMESPACE_URL, f"event:{run_id}:{thread_id}:{sequence}"),
                    run_id=run_id,
                    thread_id=thread_id,
                    sequence=sequence,
                    occurred_at=occurred_at,
                    stage=item.get("node"),
                    event_type=item.get("event") or "UNKNOWN",
                    level=item.get("level") or "INFO",
                    payload=_json_safe(item),
                )
                session.execute(
                    event_statement.on_conflict_do_update(
                        index_elements=["run_id", "thread_id", "sequence"],
                        set_={
                            "occurred_at": event_statement.excluded.occurred_at,
                            "stage": event_statement.excluded.stage,
                            "event_type": event_statement.excluded.event_type,
                            "level": event_statement.excluded.level,
                            "payload": event_statement.excluded.payload,
                        },
                    )
                )


def create_run_record(
    settings: Settings,
    *,
    run_id: str,
    thread_id: str,
    source: str,
) -> None:
    """Create the durable shell returned by POST /api/runs before execution starts."""
    if not settings.database_url:
        return
    run_uuid = _as_uuid(run_id)
    thread_uuid = _as_uuid(thread_id)
    initial_result = {
        "run_id": str(run_uuid),
        "thread_id": str(thread_uuid),
        "source": source,
        "status": "CREATED",
        "outcome": None,
    }
    with Session(get_engine(settings.database_url)) as session, session.begin():
        session.add(
            Run(
                id=run_uuid,
                owner_id=settings.owner_id,
                status="CREATED",
                provider=settings.provider,
                model=settings.model_name,
            )
        )
        session.flush()
        session.add(
            InvoiceRecord(
                id=uuid5(NAMESPACE_URL, f"invoice:{thread_uuid}"),
                run_id=run_uuid,
                thread_id=thread_uuid,
                source_path=source,
                status="CREATED",
                result_data=initial_result,
            )
        )


def set_run_status(settings: Settings, run_id: str, status: str) -> None:
    if not settings.database_url:
        return
    run_uuid = _as_uuid(run_id)
    with Session(get_engine(settings.database_url)) as session, session.begin():
        session.execute(
            update(Run)
            .where(Run.id == run_uuid, Run.owner_id == settings.owner_id)
            .values(status=status, updated_at=func.now())
        )
        invoice_values: dict[str, Any] = {"status": status, "updated_at": func.now()}
        if status == "RUNNING":
            invoice_values["outcome"] = None
        session.execute(
            update(InvoiceRecord)
            .where(InvoiceRecord.run_id == run_uuid)
            .values(**invoice_values)
        )


def record_run_failure(settings: Settings, run_id: str, error: str) -> None:
    if not settings.database_url:
        return
    run_uuid = _as_uuid(run_id)
    with Session(get_engine(settings.database_url)) as session, session.begin():
        invoice = session.execute(
            select(InvoiceRecord).where(InvoiceRecord.run_id == run_uuid)
        ).scalar_one_or_none()
        if invoice is None:
            return
        result = dict(invoice.result_data or {})
        result.update({"status": "FAILED", "outcome": None, "error": error})
        invoice.result_data = result
        invoice.status = "FAILED"
        invoice.outcome = None


def get_run_record(settings: Settings, run_id: str) -> dict[str, Any] | None:
    if not settings.database_url:
        return None
    try:
        run_uuid = _as_uuid(run_id)
    except (TypeError, ValueError):
        return None
    with Session(get_engine(settings.database_url)) as session:
        row = session.execute(
            select(Run, InvoiceRecord)
            .join(InvoiceRecord, InvoiceRecord.run_id == Run.id)
            .where(Run.id == run_uuid, Run.owner_id == settings.owner_id)
        ).first()
        if row is None:
            return None
        run, invoice = row
        result = dict(invoice.result_data or {})
        result.update(
            {
                "run_id": str(run.id),
                "thread_id": str(invoice.thread_id),
                "status": run.status,
                "outcome": invoice.outcome,
                "source": invoice.source_path,
                "provider": run.provider,
                "model": run.model,
                "created_at": run.created_at.isoformat(),
                "updated_at": run.updated_at.isoformat(),
            }
        )
        return result


def list_run_records(
    settings: Settings,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if not settings.database_url:
        return []
    with Session(get_engine(settings.database_url)) as session:
        rows = session.execute(
            select(Run, InvoiceRecord)
            .join(InvoiceRecord, InvoiceRecord.run_id == Run.id)
            .where(Run.owner_id == settings.owner_id)
            .order_by(Run.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        records = []
        for run, invoice in rows:
            records.append(
                {
                    "run_id": str(run.id),
                    "thread_id": str(invoice.thread_id),
                    "status": run.status,
                    "outcome": invoice.outcome,
                    "invoice_id": invoice.invoice_number,
                    "source": invoice.source_path,
                    "provider": run.provider,
                    "model": run.model,
                    "created_at": run.created_at.isoformat(),
                    "updated_at": run.updated_at.isoformat(),
                }
            )
        return records


def append_run_event(settings: Settings, event_payload: dict[str, Any]) -> None:
    if not settings.database_url:
        return
    occurred_at = _parse_datetime(event_payload.get("timestamp"))
    statement = postgres_insert(RunEvent).values(
        id=_as_uuid(event_payload["event_id"]),
        run_id=_as_uuid(event_payload["run_id"]),
        thread_id=_as_uuid(event_payload["thread_id"]),
        sequence=int(event_payload["sequence"]),
        occurred_at=occurred_at,
        stage=event_payload.get("stage"),
        event_type=event_payload["event_type"],
        level=event_payload.get("level") or "INFO",
        payload=_json_safe(event_payload),
    )
    with get_engine(settings.database_url).begin() as conn:
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=["run_id", "thread_id", "sequence"],
                set_={
                    "occurred_at": statement.excluded.occurred_at,
                    "stage": statement.excluded.stage,
                    "event_type": statement.excluded.event_type,
                    "level": statement.excluded.level,
                    "payload": statement.excluded.payload,
                },
            )
        )


def list_run_events(
    settings: Settings,
    run_id: str,
    *,
    after_sequence: int = 0,
) -> list[dict[str, Any]]:
    if not settings.database_url:
        return []
    run_uuid = _as_uuid(run_id)
    with Session(get_engine(settings.database_url)) as session:
        rows = session.execute(
            select(RunEvent.payload)
            .join(Run, Run.id == RunEvent.run_id)
            .where(
                RunEvent.run_id == run_uuid,
                RunEvent.sequence > after_sequence,
                Run.owner_id == settings.owner_id,
            )
            .order_by(RunEvent.sequence)
        ).scalars()
        return [dict(item) for item in rows]


def latest_run_event_sequence(settings: Settings, run_id: str) -> int:
    if not settings.database_url:
        return 0
    with Session(get_engine(settings.database_url)) as session:
        value = session.execute(
            select(func.max(RunEvent.sequence)).where(RunEvent.run_id == _as_uuid(run_id))
        ).scalar_one()
        return int(value or 0)


def list_run_snapshots(settings: Settings) -> list[dict[str, Any]]:
    if not settings.database_url:
        return []
    with Session(get_engine(settings.database_url)) as session:
        rows = session.execute(
            select(InvoiceRecord.result_data)
            .join(Run, Run.id == InvoiceRecord.run_id)
            .where(Run.owner_id == settings.owner_id)
            .order_by(InvoiceRecord.updated_at.desc())
        ).scalars()
        return list(rows)


def migrate_sqlite_data(settings: Settings, sqlite_path: Path) -> dict[str, int]:
    """Copy legacy inventory and ledgers into an initialized PostgreSQL database."""
    if not settings.database_url:
        raise ValueError("DATABASE_URL is required for SQLite migration")
    ensure_database(settings)
    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    try:
        with Session(get_engine(settings.database_url)) as session, session.begin():
            table_models = {
                "inventory": Inventory,
                "vendors": Vendor,
                "fx_rates": FxRate,
                "processed_invoices": ProcessedInvoice,
                "human_decisions": HumanReview,
                "payment_attempts": PaymentAttempt,
            }
            for table, model in table_models.items():
                exists = source.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if not exists:
                    counts[table] = 0
                    continue
                rows = [dict(row) for row in source.execute(f"SELECT * FROM {table}").fetchall()]
                counts[table] = len(rows)
                for row in rows:
                    if table == "vendors":
                        row["aliases"] = json.loads(row.get("aliases") or "[]")
                    elif table == "processed_invoices":
                        row["revision"] = row.get("revision") or ""
                        row["source_path"] = row.get("source_path") or ""
                        if row.get("run_id"):
                            row["run_id"] = _as_uuid(row["run_id"])
                        else:
                            row["run_id"] = None
                        row["created_at"] = _parse_datetime(row.get("created_at"))
                    elif table == "human_decisions":
                        row["thread_id"] = _as_uuid(row["thread_id"])
                        row["decided_at"] = _parse_datetime(row.get("decided_at"))
                    elif table == "payment_attempts":
                        row["response"] = json.loads(row.get("response") or "{}")
                        row["created_at"] = _parse_datetime(row.get("created_at"))
                    statement = postgres_insert(model).values(**row)
                    session.execute(statement.on_conflict_do_nothing())
    finally:
        source.close()
    return counts


def _parse_datetime(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        return datetime.fromisoformat(value)
    return datetime.now(UTC)
