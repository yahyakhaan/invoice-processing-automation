from __future__ import annotations

import csv
import io
from pathlib import Path

from invoice_agent.parsers.common import looks_like_total_label, parse_date, parse_money, parse_number
from invoice_agent.schemas import InvoiceDraft, LineItem


def _rows(source: str | Path) -> list[list[str]]:
    raw = Path(source).read_text(encoding="utf-8") if Path(str(source)).exists() else str(source)
    return list(csv.reader(io.StringIO(raw)))


def parse_csv_invoice(source: str | Path) -> InvoiceDraft:
    table = _rows(source)
    if not table:
        return InvoiceDraft(source_format="csv", confidence=0.1)
    header = [c.strip() for c in table[0]]
    if len(header) == 2 and header[0].lower() == "field" and header[1].lower() == "value":
        return _parse_pivoted(table[1:])
    return _parse_tabular(header, table[1:])


def _parse_pivoted(pairs: list[list[str]]) -> InvoiceDraft:
    fields: dict[str, str] = {}
    items: list[LineItem] = []
    current: dict[str, str] = {}
    for row in pairs:
        if len(row) < 2:
            continue
        key, value = row[0].strip(), row[1].strip()
        if key in {"item", "quantity", "unit_price"}:
            current[key] = value
            if {"item", "quantity"} <= set(current):
                if "unit_price" in current or key == "unit_price":
                    name = current.get("item", "")
                    items.append(
                        LineItem(
                            sku=name,
                            raw_name=name,
                            quantity=parse_number(current.get("quantity")) or 0.0,
                            unit_price=parse_money(current.get("unit_price")),
                        )
                    )
                    current = {}
            continue
        fields[key] = value
        if current.get("item") and current.get("quantity") and key not in {"item", "quantity", "unit_price"}:
            name = current["item"]
            items.append(
                LineItem(
                    sku=name,
                    raw_name=name,
                    quantity=parse_number(current.get("quantity")) or 0.0,
                    unit_price=parse_money(current.get("unit_price")),
                )
            )
            current = {}
    if current.get("item"):
        name = current["item"]
        items.append(
            LineItem(
                sku=name,
                raw_name=name,
                quantity=parse_number(current.get("quantity")) or 0.0,
                unit_price=parse_money(current.get("unit_price")),
            )
        )
    return InvoiceDraft(
        invoice_number=fields.get("invoice_number"),
        vendor_raw=fields.get("vendor"),
        invoice_date=parse_date(fields.get("date")),
        due_date=parse_date(fields.get("due_date")),
        line_items=items,
        subtotal=parse_money(fields.get("subtotal")),
        tax=parse_money(fields.get("tax")),
        total=parse_money(fields.get("total")),
        payment_terms=fields.get("payment_terms"),
        source_format="csv-pivoted",
        confidence=0.95,
        evidence_spans=["csv-pivoted"],
    )


def _col(header: list[str], *names: str) -> int | None:
    lower = [h.lower() for h in header]
    for name in names:
        if name.lower() in lower:
            return lower.index(name.lower())
    return None


def _parse_tabular(header: list[str], rows: list[list[str]]) -> InvoiceDraft:
    idx_num = _col(header, "Invoice Number", "invoice_number")
    idx_vendor = _col(header, "Vendor")
    idx_date = _col(header, "Date")
    idx_due = _col(header, "Due Date", "due_date")
    idx_item = _col(header, "Item")
    idx_qty = _col(header, "Qty", "quantity")
    idx_price = _col(header, "Unit Price", "unit_price")
    idx_line = _col(header, "Line Total", "amount")
    items: list[LineItem] = []
    meta: dict[str, str] = {}
    totals: dict[str, float | None] = {}
    for row in rows:
        padded = row + [""] * (len(header) - len(row))
        item_val = padded[idx_item] if idx_item is not None else ""
        qty_val = padded[idx_qty] if idx_qty is not None else ""
        first_nonempty = next((c for c in padded if c.strip()), "")
        if (not item_val.strip() or looks_like_total_label(item_val)) and looks_like_total_label(
            first_nonempty
        ) or looks_like_total_label(padded[-2] if len(padded) >= 2 else ""):
            label = next((c for c in padded if looks_like_total_label(c)), first_nonempty)
            amount = parse_money(padded[-1])
            low = label.lower()
            if low.startswith("subtotal"):
                totals["subtotal"] = amount
            elif low.startswith("tax"):
                totals["tax"] = amount
            elif low.startswith("total") or low.startswith("grand"):
                totals["total"] = amount
            continue
        if not item_val.strip() or qty_val.strip() == "":
            continue
        items.append(
            LineItem(
                sku=item_val.strip(),
                raw_name=item_val.strip(),
                quantity=parse_number(qty_val) or 0.0,
                unit_price=parse_money(padded[idx_price] if idx_price is not None else None),
                amount=parse_money(padded[idx_line] if idx_line is not None else None),
            )
        )
        if idx_num is not None and padded[idx_num].strip():
            meta["invoice_number"] = padded[idx_num].strip()
        if idx_vendor is not None and padded[idx_vendor].strip():
            meta["vendor"] = padded[idx_vendor].strip()
        if idx_date is not None and padded[idx_date].strip():
            meta["date"] = padded[idx_date].strip()
        if idx_due is not None and padded[idx_due].strip():
            meta["due"] = padded[idx_due].strip()
    return InvoiceDraft(
        invoice_number=meta.get("invoice_number"),
        vendor_raw=meta.get("vendor"),
        invoice_date=parse_date(meta.get("date")),
        due_date=parse_date(meta.get("due")),
        line_items=items,
        subtotal=totals.get("subtotal"),
        tax=totals.get("tax"),
        total=totals.get("total"),
        source_format="csv-tabular",
        confidence=0.95,
        evidence_spans=["csv-tabular"],
    )
