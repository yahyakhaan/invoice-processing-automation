from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from invoice_agent.config import Settings
from invoice_agent.observability.console import capture_progress
from invoice_agent.orchestration.graph import InvoicePipeline
from invoice_agent.runner import result_payload
from invoice_agent.services.demo import hitl_demo_invoice
from invoice_agent.storage import (
    append_run_event,
    count_agentic_runs_since,
    create_run_record,
    get_run_record,
    latest_run_event_sequence,
    list_run_events,
    list_run_records,
    record_run_failure,
    record_run_snapshot,
    set_run_status,
)
from invoice_api.sources import PreparedSource

TERMINAL_STATUSES = {"COMPLETED", "FAILED"}
SENSITIVE_EVENT_KEYS = {
    "authorization",
    "api_key",
    "database_url",
    "langgraph_aes_key",
    "password",
    "raw_text",
    "refresh_token",
    "secret",
    "session_secret",
    "token",
    "access_token",
}


@dataclass
class ActiveRun:
    record: dict[str, Any]
    settings: Settings
    events: list[dict[str, Any]] = field(default_factory=list)
    sequence: int = 0
    busy: bool = False


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _status_for_outcome(outcome: str | None) -> str:
    if outcome == "PENDING_VP_REVIEW":
        return "PENDING_REVIEW"
    if outcome in {"INGEST_FAILED", "PAYMENT_FAILED"}:
        return "FAILED"
    if outcome:
        return "COMPLETED"
    return "RUNNING"


def _public_outcome(status: str, outcome: str | None) -> str | None:
    return None if status == "PENDING_REVIEW" and outcome == "PENDING_VP_REVIEW" else outcome


def sanitize_event_data(value: Any, *, key: str | None = None) -> Any:
    normalized_key = (key or "").lower().replace("-", "_")
    if normalized_key in SENSITIVE_EVENT_KEYS or normalized_key.endswith("_secret"):
        return "[REDACTED]"
    if normalized_key in {"path", "source_path"} and isinstance(value, str):
        return Path(value).name
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_event_data(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_event_data(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_event_data(item) for item in value]
    return value


def _stage_from_progress(message: str) -> str | None:
    if not message.startswith("stage: "):
        return None
    return message.removeprefix("stage: ").split()[0].upper()


class RunService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._active: dict[str, ActiveRun] = {}
        self._lock = RLock()
        self._budget_day = datetime.now(UTC).date()
        self._agentic_runs_today = self._persisted_agentic_runs_today()

    def demo_status(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_budget_day()
            enabled = self.settings.agentic_mode and self.settings.demo_llm_daily_limit > 0
            remaining = (
                max(0, self.settings.demo_llm_daily_limit - self._agentic_runs_today)
                if enabled
                else 0
            )
            return {
                "llm_enabled": enabled,
                "llm_daily_limit": self.settings.demo_llm_daily_limit,
                "llm_runs_remaining": remaining,
            }

    def _persisted_agentic_runs_today(self) -> int:
        start = datetime.combine(self._budget_day, datetime.min.time(), tzinfo=UTC)
        return count_agentic_runs_since(self.settings, start)

    def _refresh_budget_day(self) -> None:
        today = datetime.now(UTC).date()
        if today != self._budget_day:
            self._budget_day = today
            self._agentic_runs_today = self._persisted_agentic_runs_today()

    def _claim_run_settings(self) -> Settings:
        with self._lock:
            self._refresh_budget_day()
            if (
                self.settings.agentic_mode
                and self._agentic_runs_today < self.settings.demo_llm_daily_limit
            ):
                self._agentic_runs_today += 1
                return self.settings
        return self.settings.model_copy(
            update={"provider_override": "mock", "require_llm": False}
        )

    def _settings_for_record(self, record: dict[str, Any]) -> Settings:
        if record.get("agentic_mode"):
            return self.settings
        return self.settings.model_copy(
            update={"provider_override": "mock", "require_llm": False}
        )

    def create(self, source: PreparedSource) -> dict[str, Any]:
        run_settings = self._claim_run_settings()
        run_id = str(uuid4())
        thread_id = str(uuid4())
        now = _utcnow()
        record = {
            "run_id": run_id,
            "thread_id": thread_id,
            "status": "CREATED",
            "outcome": None,
            "invoice_id": None,
            "source": source.label,
            "provider": run_settings.provider,
            "model": run_settings.model_name,
            "created_at": now,
            "updated_at": now,
            "agentic_mode": run_settings.agentic_mode,
        }
        create_run_record(
            run_settings,
            run_id=run_id,
            thread_id=thread_id,
            source=source.label,
        )
        with self._lock:
            self._active[run_id] = ActiveRun(
                record=record,
                settings=run_settings,
                busy=True,
            )
        self._emit(
            run_id,
            event_type="RUN_CREATED",
            message="Invoice run created",
            status="CREATED",
            data={
                "source": source.label,
                "execution_mode": "llm" if run_settings.agentic_mode else "deterministic",
                "llm_runs_remaining": self.demo_status()["llm_runs_remaining"],
            },
        )
        return deepcopy(record)

    def execute(self, run_id: str, source: PreparedSource) -> None:
        try:
            run_settings = self._active[run_id].settings
            self._set_status(run_id, "RUNNING")
            self._emit(
                run_id,
                event_type="RUN_STARTED",
                message="Invoice processing started",
                status="RUNNING",
            )
            record = self.get(run_id)
            if record is None:
                raise RuntimeError("Run disappeared before execution")
            thread_id = record["thread_id"]

            def on_progress(message: str, detail: str | None) -> None:
                self._emit(
                    run_id,
                    event_type="PROGRESS",
                    message=message,
                    status="RUNNING",
                    stage=_stage_from_progress(message),
                    data={"detail": detail} if detail else {},
                )

            def on_event(item: dict[str, Any]) -> None:
                self._emit_graph_event(run_id, item)

            with capture_progress(on_progress), InvoicePipeline(run_settings) as pipeline:
                if source.demo:
                    invoice = hitl_demo_invoice(thread_id)
                    values = pipeline.run(
                        thread_id=thread_id,
                        payload=self._initial_payload(
                            run_id,
                            thread_id,
                            raw_path=None,
                            demo=True,
                            invoice=invoice.model_dump(),
                            run_settings=run_settings,
                        ),
                        on_event=on_event,
                    )
                else:
                    if source.path is None:
                        raise RuntimeError("Invoice source path is missing")
                    values = pipeline.run(
                        thread_id=thread_id,
                        payload=self._initial_payload(
                            run_id,
                            thread_id,
                            raw_path=str(source.path),
                            run_settings=run_settings,
                        ),
                        on_event=on_event,
                    )
            self._finish(run_id, values, source.label, run_settings)
        except Exception as exc:  # noqa: BLE001
            self._fail(run_id, exc)
        finally:
            source.cleanup()

    def begin_review(self, run_id: str) -> dict[str, Any] | None:
        record = self.get(run_id)
        if record is None:
            return None
        with self._lock:
            active = self._active.get(run_id)
            if active is None:
                active = ActiveRun(
                    record=deepcopy(record),
                    settings=self._settings_for_record(record),
                    sequence=latest_run_event_sequence(self.settings, run_id),
                )
                self._active[run_id] = active
            if active.busy or active.record["status"] != "PENDING_REVIEW":
                return deepcopy(active.record)
            active.busy = True
        self._set_status(run_id, "RUNNING")
        self._emit(
            run_id,
            event_type="REVIEW_ACCEPTED",
            message="VP review received; workflow is resuming",
            status="RUNNING",
            stage="HUMAN_REVIEW",
        )
        return self.get(run_id)

    def resume(
        self,
        run_id: str,
        *,
        decision: str,
        actor: str,
        rationale: str,
    ) -> None:
        record = self.get(run_id)
        if record is None:
            return
        try:
            run_settings = self._settings_for_record(record)
            with capture_progress(
                lambda message, detail: self._emit(
                    run_id,
                    event_type="PROGRESS",
                    message=message,
                    status="RUNNING",
                    stage=_stage_from_progress(message),
                    data={"detail": detail} if detail else {},
                )
            ), InvoicePipeline(run_settings) as pipeline:
                values = pipeline.resume(
                    thread_id=record["thread_id"],
                    decision=decision,
                    actor=actor,
                    rationale=rationale,
                    on_event=lambda item: self._emit_graph_event(run_id, item),
                )
            self._finish(run_id, values, record.get("source"), run_settings)
        except Exception as exc:  # noqa: BLE001
            self._fail(run_id, exc)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            active = self._active.get(run_id)
            if active is not None:
                return self._normalize_record(deepcopy(active.record))
        record = get_run_record(self.settings, run_id)
        return self._normalize_record(record) if record else None

    def list(self, *, limit: int, offset: int) -> list[dict[str, Any]]:
        persisted = list_run_records(self.settings, limit=limit + offset, offset=0)
        with self._lock:
            active_records = [deepcopy(item.record) for item in self._active.values()]
        merged = {item["run_id"]: item for item in persisted}
        merged.update({item["run_id"]: item for item in active_records})
        ordered = sorted(merged.values(), key=lambda item: item["created_at"], reverse=True)
        return [self._normalize_record(item) for item in ordered[offset : offset + limit]]

    def events(self, run_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        if self.settings.database_url:
            return list_run_events(self.settings, run_id, after_sequence=after_sequence)
        with self._lock:
            active = self._active.get(run_id)
            if active is None:
                return []
            return [deepcopy(item) for item in active.events if item["sequence"] > after_sequence]

    def _initial_payload(
        self,
        run_id: str,
        thread_id: str,
        *,
        run_settings: Settings,
        raw_path: str | None,
        demo: bool = False,
        invoice: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "raw_path": raw_path,
            "demo_mode": demo,
            "skip_ingest": demo,
            "invoice": invoice,
            "invoice_draft": None,
            "events": [],
            "tool_trace": [],
            "agent_messages": [],
            "ingest_retry_count": 0,
            "validation_retry_count": 0,
            "approval_revision_count": 0,
            "agentic_mode": run_settings.agentic_mode,
            "run_id": run_id,
            "thread_id": thread_id,
            "provider": run_settings.provider,
            "model": run_settings.model_name,
            "model_calls": 0,
        }

    def _finish(
        self,
        run_id: str,
        values: dict[str, Any],
        source: str | None,
        run_settings: Settings,
    ) -> None:
        payload = result_payload(values, run_settings, source)
        status = _status_for_outcome(payload.get("outcome"))
        public_outcome = _public_outcome(status, payload.get("outcome"))
        event_type = "RUN_PENDING_REVIEW" if status == "PENDING_REVIEW" else "RUN_FINISHED"
        message = "VP review is required" if status == "PENDING_REVIEW" else "Invoice processing finished"
        self._emit(
            run_id,
            event_type=event_type,
            message=message,
            status=status,
            data={"outcome": public_outcome},
        )
        record_run_snapshot(self.settings, payload, persist_events=False)
        payload.update(
            {
                "status": status,
                "outcome": public_outcome,
                "updated_at": _utcnow(),
            }
        )
        with self._lock:
            active = self._active[run_id]
            payload.setdefault("created_at", active.record["created_at"])
            active.record = {**active.record, **payload}
            active.busy = False

    def _fail(self, run_id: str, exc: Exception) -> None:
        error = f"{type(exc).__name__}: invoice processing failed"
        self._emit(
            run_id,
            event_type="RUN_FAILED",
            message="Invoice processing failed",
            status="FAILED",
            level="ERROR",
            data={"error_type": type(exc).__name__},
        )
        self._set_status(run_id, "FAILED")
        record_run_failure(self.settings, run_id, error)
        with self._lock:
            active = self._active.get(run_id)
            if active is not None:
                active.record["error"] = error
                active.record["updated_at"] = _utcnow()
                active.busy = False

    def _set_status(self, run_id: str, status: str) -> None:
        set_run_status(self.settings, run_id, status)
        with self._lock:
            active = self._active.get(run_id)
            if active is not None:
                active.record["status"] = status
                active.record["updated_at"] = _utcnow()

    def _emit_graph_event(self, run_id: str, item: dict[str, Any]) -> None:
        self._emit(
            run_id,
            event_type=item.get("event") or "WORKFLOW_EVENT",
            message=(item.get("event") or "Workflow event").replace("_", " ").title(),
            status=self.get(run_id)["status"],
            stage=(item.get("node") or "").upper() or None,
            level=item.get("level") or "INFO",
            data=item.get("data") or {},
            timestamp=item.get("ts"),
        )

    def _emit(
        self,
        run_id: str,
        *,
        event_type: str,
        message: str,
        status: str,
        stage: str | None = None,
        level: str = "INFO",
        data: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            active = self._active[run_id]
            active.sequence += 1
            sequence = active.sequence
            thread_id = active.record["thread_id"]
            payload = {
                "event_id": str(uuid5(NAMESPACE_URL, f"api-event:{run_id}:{sequence}")),
                "run_id": run_id,
                "thread_id": thread_id,
                "sequence": sequence,
                "timestamp": timestamp or _utcnow(),
                "stage": stage,
                "event_type": event_type,
                "status": status,
                "level": level,
                "message": message,
                "data": sanitize_event_data(data or {}),
            }
            active.events.append(payload)
        append_run_event(self.settings, payload)
        return deepcopy(payload)

    @staticmethod
    def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(record)
        status = normalized.get("status") or _status_for_outcome(normalized.get("outcome"))
        normalized["status"] = status
        normalized["outcome"] = _public_outcome(status, normalized.get("outcome"))
        normalized.setdefault("invoice_id", normalized.get("invoice_id"))
        normalized.setdefault("agentic_mode", False)
        normalized.setdefault("model_calls", 0)
        normalized.setdefault("tool_calls", 0)
        return normalized
