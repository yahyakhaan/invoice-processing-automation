from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from invoice_agent.schemas import Invoice, PaymentResult, ValidationReport

console = Console(stderr=True)


def progress(message: str, *, detail: str | None = None) -> None:
    """Live step line for demos — flush immediately so LLM waits are not silent."""
    if detail:
        console.print(f"[cyan]→[/cyan] {message}  [dim]{detail}[/dim]", highlight=False)
    else:
        console.print(f"[cyan]→[/cyan] {message}", highlight=False)
    console.file.flush()


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, default=str) + "\n")


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


def render_console(result: dict[str, Any]) -> None:
    agentic = result.get("agentic_mode")
    banner = "LIVE LLM" if agentic else "DETERMINISTIC FALLBACK — NO LLM"
    outcome = result.get("outcome", "UNKNOWN")
    tree = Tree(f"[bold]{result.get('invoice_id', 'invoice')}[/bold] → {outcome}")
    tree.add(f"provider={result.get('provider')} model={result.get('model')}  [{banner}]")
    invoice = result.get("invoice") or {}
    tree.add(
        f"vendor={invoice.get('vendor_canonical')} total={invoice.get('total')} {invoice.get('currency')}"
    )
    flags = (result.get("validation") or {}).get("flags") or []
    flag_table = Table(title="Flags")
    flag_table.add_column("code")
    flag_table.add_column("severity")
    flag_table.add_column("message")
    for flag in flags:
        flag_table.add_row(flag.get("code", ""), flag.get("severity", ""), flag.get("message", ""))
    if flags:
        tree.add("validation flags recorded")
    draft = result.get("approval_draft") or {}
    final = result.get("approval_final") or {}
    critique = result.get("approval_critique") or {}
    tree.add(f"approval draft={draft.get('decision')} final={final.get('decision')}")
    tree.add(f"critic={critique.get('verdict')}")
    human = result.get("human_review")
    if human:
        tree.add(f"VP {human.get('decision')} by {human.get('actor')}")
    payment = result.get("payment")
    if payment:
        tree.add(f"payment {payment.get('status')} {payment.get('amount')} → {payment.get('vendor')}")
    color = "green" if outcome in {"PAY", "PAID"} else "yellow" if outcome == "PENDING_VP_REVIEW" else "red"
    console.print(Panel(tree, title=banner, border_style=color))
    if flags:
        console.print(flag_table)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
