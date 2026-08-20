from __future__ import annotations

from typing import Any

from invoice_agent.observability.logging import event


def handoff(
    *,
    run_id: str,
    invoice_id: str | None,
    source: str,
    target: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return event(
        run_id=run_id,
        invoice_id=invoice_id,
        event_name="HANDOFF",
        agent_id=source,
        data={"from": source, "to": target, **(data or {})},
    )
