from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from invoice_agent.parsers.common import parse_date, parse_money, parse_number
from invoice_agent.schemas import InvoiceDraft, LineItem


def _text(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    return node.text.strip()


def parse_xml_invoice(source: str | Path) -> InvoiceDraft:
    path = Path(source)
    raw = path.read_text(encoding="utf-8") if path.exists() else str(source)
    root = ET.fromstring(raw)
    header = root.find("header") if root.find("header") is not None else root
    items = []
    for item in root.findall(".//line_items/item") or root.findall(".//item"):
        name = _text(item.find("name")) or _text(item.find("item")) or ""
        items.append(
            LineItem(
                sku=name,
                raw_name=name,
                quantity=parse_number(_text(item.find("quantity"))) or 0.0,
                unit_price=parse_money(_text(item.find("unit_price"))),
                amount=parse_money(_text(item.find("amount"))),
            )
        )
    totals = root.find("totals")
    return InvoiceDraft(
        invoice_number=_text(header.find("invoice_number")),
        vendor_raw=_text(header.find("vendor")),
        currency=_text(header.find("currency")) or "USD",
        invoice_date=parse_date(_text(header.find("date"))),
        due_date=parse_date(_text(header.find("due_date"))),
        line_items=items,
        subtotal=parse_money(_text(totals.find("subtotal")) if totals is not None else None),
        tax=parse_money(_text(totals.find("tax_amount")) if totals is not None else None),
        total=parse_money(_text(totals.find("total")) if totals is not None else None),
        payment_terms=_text(root.find("payment_terms")),
        source_format="xml",
        confidence=0.99,
        raw_text=raw,
        evidence_spans=["xml-etree"],
    )
