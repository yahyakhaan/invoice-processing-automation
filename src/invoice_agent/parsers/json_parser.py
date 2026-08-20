from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from invoice_agent.parsers.common import parse_date, parse_money, parse_number
from invoice_agent.schemas import InvoiceDraft, LineItem


def _vendor_name(vendor: Any) -> str | None:
    if vendor is None:
        return None
    if isinstance(vendor, dict):
        name = vendor.get("name")
        return None if name is None else str(name)
    return str(vendor)


def parse_json_invoice(source: str | Path | dict) -> InvoiceDraft:
    if isinstance(source, dict):
        data = source
        raw = json.dumps(source)
    else:
        path = Path(str(source))
        raw = path.read_text(encoding="utf-8") if path.exists() else str(source)
        data = json.loads(raw)

    items = []
    for row in data.get("line_items") or data.get("items") or []:
        name = str(row.get("item") or row.get("name") or "")
        items.append(
            LineItem(
                sku=name,
                raw_name=name,
                quantity=parse_number(row.get("quantity")) or 0.0,
                unit_price=parse_money(row.get("unit_price")),
                amount=parse_money(row.get("amount") or row.get("line_total")),
                note=row.get("note"),
            )
        )
    vendor = _vendor_name(data.get("vendor"))
    return InvoiceDraft(
        invoice_number=data.get("invoice_number") or data.get("invoice"),
        revision=data.get("revision"),
        vendor_raw=vendor,
        currency=data.get("currency") or "USD",
        invoice_date=parse_date(data.get("date") or data.get("invoice_date")),
        due_date=parse_date(data.get("due_date")),
        line_items=items,
        subtotal=parse_money(data.get("subtotal")),
        tax=parse_money(data.get("tax_amount") or data.get("tax")),
        shipping=parse_money(data.get("shipping")),
        total=parse_money(data.get("total") or data.get("total_amount")),
        payment_terms=data.get("payment_terms") or None,
        source_format="json",
        confidence=0.99,
        raw_text=raw,
        evidence_spans=["json-map"],
    )
