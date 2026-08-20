from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from invoice_agent.schemas import LogEvent


def event(
    *,
    run_id: str,
    event_name: str,
    invoice_id: str | None = None,
    agent_id: str | None = None,
    node: str | None = None,
    tool: str | None = None,
    attempt: int | None = None,
    level: str = "INFO",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return LogEvent(
        ts=datetime.now(timezone.utc).isoformat(),
        run_id=run_id,
        invoice_id=invoice_id,
        agent_id=agent_id,
        node=node,
        tool=tool,
        attempt=attempt,
        level=level,
        event=event_name,
        data=data or {},
    ).model_dump()
