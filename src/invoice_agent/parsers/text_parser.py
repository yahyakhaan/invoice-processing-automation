from __future__ import annotations

import re
from pathlib import Path

from invoice_agent.parsers.common import (
    apply_ocr_fixes,
    normalize_invoice_number,
    parse_date,
    parse_money,
    parse_number,
    strip_email_envelope,
)
from invoice_agent.schemas import InvoiceDraft, LineItem

FRAUD_PATTERNS = [
    (r"urgent", "URGENT_LANGUAGE"),
    (r"wire transfer preferred", "WIRE_CHANGE"),
    (r"pay immediately", "PAY_IMMEDIATELY"),
    (r"avoid penalties", "PENALTY_THREAT"),
]


def parse_text_invoice(source: str | Path, *, already_text: bool = False) -> InvoiceDraft:
    raw = source if already_text or not Path(str(source)).exists() else Path(source).read_text(encoding="utf-8")
    text = apply_ocr_fixes(str(raw))
    body = strip_email_envelope(text)
    fraud = [code for pat, code in FRAUD_PATTERNS if re.search(pat, body, re.I)]
    vendor = _search(body, r"(?:vendor|vndr)\s*[:#]?\s*(.+)")
    if not vendor:
        vendor = _search(body, r"^\s*FROM:\s*(.+)$")
    if vendor:
        vendor = vendor.split("\n")[0].strip()
        vendor = re.sub(r"^\s*FROM:\s*", "", vendor, flags=re.I)
    number = _invoice_number(body)
    invoice_date = parse_date(_search(body, r"(?:invoice\s*)?(?:date|dt)\s*[:#]?\s*([A-Za-z0-9,\-/ ]+)"))
    due = parse_date(
        _search(body, r"(?:due\s*(?:date|dt)?|due)\s*[:#]?\s*([A-Za-z0-9,\-/ ]+)")
    )
    subtotal = parse_money(_search(body, r"subtotal\s*:?\s*(\$?[0-9,O.]+)"))
    tax = parse_money(_search(body, r"(?:sales\s*)?tax(?:\s*\([^)]+\))?\s*:?\s*(\$?[0-9,O.]+)"))
    shipping = parse_money(_search(body, r"shipping\s*:?\s*(\$?[0-9,O.]+)"))
    total = parse_money(
        _search(body, r"(?:total\s*amount|grand\s*total|(?<![A-Za-z])amt|(?<![A-Za-z])total)\s*:?\s*(\$?[0-9,O.]+)")
    )
    terms = _search(body, r"(?:payment\s*terms|pymnt\s*terms|terms)\s*:?\s*(.+)")
    items = _parse_items(body)
    unresolved = []
    if not vendor:
        unresolved.append("vendor")
    if not number:
        unresolved.append("invoice_number")
    if due is None:
        unresolved.append("due_date")
    if not items:
        unresolved.append("line_items")
    if total is None:
        unresolved.append("total")
    confidence = 0.9 if not unresolved else 0.55
    if fraud:
        confidence = min(confidence, 0.7)
    return InvoiceDraft(
        invoice_number=normalize_invoice_number(number) if number else None,
        vendor_raw=vendor,
        invoice_date=invoice_date,
        due_date=due,
        line_items=items,
        subtotal=subtotal,
        tax=tax,
        shipping=shipping,
        total=total,
        payment_terms=terms.strip() if terms else None,
        source_format="txt",
        fraud_signals=fraud,
        confidence=confidence,
        unresolved_fields=unresolved,
        evidence_spans=["text-heuristics"],
        raw_text=body,
    )


def _invoice_number(text: str) -> str | None:
    patterns = [
        r"invoice\s*number\s*[:#]?\s*([A-Za-z0-9 \-]+)",
        r"invoice\s*#\s*([A-Za-z0-9 \-]+)",
        r"inv(?:oice)?\s*(?:no\.?|#)\s*[:#]?\s*([A-Za-z0-9 \-]+)",
        r"invoice:\s*([A-Za-z0-9 \-]+)",
        r"\b(INV[\s\-]?\d{3,})\b",
        r"inv\s*#\s*[:#]?\s*([A-Za-z0-9 \-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.M)
        if match:
            return match.group(1).strip()
    return None


def _search(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.I | re.M)
    if not match:
        return None
    return match.group(1).strip()


def _parse_items(text: str) -> list[LineItem]:
    items: list[LineItem] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or re.match(r"^(item|description|---+|===+)", line, re.I):
            continue
        if re.match(r"^item\s+qty\b", line, re.I):
            continue
        if re.search(r"subtotal|sales tax|shipping|total|payment terms|thank you", line, re.I):
            continue
        table = re.search(
            r"^([A-Za-z][A-Za-z0-9() /.-]*?)\s+(-?[\d.]+)\s+\$([\d,O.]+)\s+\$([\d,O.]+)\s*$",
            line,
        )
        if table and not re.match(r"^(from|vendor|invoice|date|due|to|notes|terms)\b", line, re.I):
            items.append(
                _item(table.group(1), table.group(2), table.group(3), amount=table.group(4))
            )
            continue
        labeled = re.search(
            r"^(?:[-•]\s*)?(.+?)\s+qty:?\s*(-?[\d.]+)\s+(?:unit price|@)\s*:?\s*\$?([\d,O.]+)",
            line,
            re.I,
        )
        if labeled:
            items.append(_item(labeled.group(1), labeled.group(2), labeled.group(3)))
            continue
        each = re.search(
            r"^(?:[-•]\s*)?(.+?)\s+x(\d+)\s+\$?([\d,O.]+)\s*(?:each|ea)?",
            line,
            re.I,
        )
        if each:
            items.append(_item(each.group(1), each.group(2), each.group(3)))
            continue
        qty_word = re.search(
            r"^(?:[-•]\s*)?(.+?)\s+qty\s+(-?[\d.]+)\s+@\s*\$?([\d,O.]+)",
            line,
            re.I,
        )
        if qty_word:
            items.append(_item(qty_word.group(1), qty_word.group(2), qty_word.group(3)))
            continue
    return items


def _item(name: str, qty: str, price: str, amount: str | None = None) -> LineItem:
    name = name.strip(" -")
    note = None
    note_match = re.search(r"\(([^)]+)\)", name)
    if note_match:
        note = note_match.group(1)
    return LineItem(
        sku=name,
        raw_name=name,
        quantity=parse_number(qty) or 0.0,
        unit_price=parse_money(price),
        amount=parse_money(amount),
        note=note,
    )
