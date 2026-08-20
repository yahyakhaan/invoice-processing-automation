from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from invoice_agent import storage
from invoice_agent.coerce import current_payload, parse_invoice
from invoice_agent.config import Settings
from invoice_agent.schemas import Invoice, StockCheck, ValidationFlag


def _settings(settings_json: str | None) -> Settings:
    if settings_json:
        return Settings.model_validate_json(settings_json)
    return Settings()


def _conn(settings: Settings) -> sqlite3.Connection:
    return storage.connect(Path(settings.inventory_db))


def check_required_fields(invoice_json: str | None = None) -> str:
    inv = parse_invoice(invoice_json)
    missing = []
    if not (inv.vendor_raw or "").strip():
        missing.append("vendor")
    if not (inv.invoice_number or "").strip():
        missing.append("invoice_number")
    if inv.due_date is None:
        missing.append("due_date")
    if not inv.line_items:
        missing.append("line_items")
    if any(item.quantity is None for item in inv.line_items):
        missing.append("quantities")
    amount = inv.total
    if amount is None:
        amount = inv.subtotal
    if amount is None:
        missing.append("total")
    flags = []
    if missing:
        flags.append(
            ValidationFlag(
                code="MISSING_REQUIRED_FIELD",
                severity="blocking",
                message=f"Missing required fields: {', '.join(missing)}",
                data={"fields": missing},
            ).model_dump()
        )
    return json.dumps({"flags": flags, "missing": missing})


def check_integrity(invoice_json: str | None = None) -> str:
    inv = parse_invoice(invoice_json)
    flags = []
    if (inv.vendor_raw or "").strip() == "":
        flags.append(
            ValidationFlag(
                code="DATA_INTEGRITY",
                severity="blocking",
                message="Vendor is empty",
                data={"field": "vendor"},
            ).model_dump()
        )
    if inv.total is not None and inv.total < 0:
        flags.append(
            ValidationFlag(
                code="DATA_INTEGRITY",
                severity="blocking",
                message="Negative invoice total",
                data={"total": inv.total},
            ).model_dump()
        )
    for item in inv.line_items:
        if item.quantity < 0:
            flags.append(
                ValidationFlag(
                    code="DATA_INTEGRITY",
                    severity="blocking",
                    message=f"Negative quantity for {item.sku}",
                    data={"sku": item.sku, "quantity": item.quantity},
                ).model_dump()
            )
        if item.unit_price is not None and item.unit_price < 0:
            flags.append(
                ValidationFlag(
                    code="DATA_INTEGRITY",
                    severity="blocking",
                    message=f"Negative unit price for {item.sku}",
                    data={"sku": item.sku, "unit_price": item.unit_price},
                ).model_dump()
            )
    return json.dumps({"flags": flags})


def lookup_inventory(sku: str, settings_json: str | None = None) -> str:
    settings = _settings(settings_json)
    conn = _conn(settings)
    try:
        row = storage.lookup_item(conn, sku)
    finally:
        conn.close()
    if not row:
        return json.dumps({"found": False, "sku": sku, "stock": None, "unit_cost": None})
    return json.dumps({"found": True, "sku": row["item"], "stock": row["stock"], "unit_cost": row["unit_cost"]})


def aggregate_quantities(invoice: Invoice) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for item in invoice.line_items:
        totals[item.sku] += item.quantity
    return dict(totals)


def check_stock(invoice_json: str | None = None, settings_json: str | None = None) -> str:
    inv = parse_invoice(invoice_json)
    settings = _settings(settings_json)
    aggregates = aggregate_quantities(inv)
    conn = _conn(settings)
    flags = []
    checks = []
    try:
        for sku, requested in aggregates.items():
            row = storage.lookup_item(conn, sku)
            if not row:
                checks.append(StockCheck(sku=sku, requested=requested, stock=None, found=False, ok=False).model_dump())
                flags.append(
                    ValidationFlag(
                        code="UNKNOWN_ITEM",
                        severity="blocking",
                        message=f"{sku} is not in inventory",
                        data={"sku": sku, "requested": requested},
                    ).model_dump()
                )
                continue
            stock = int(row["stock"])
            ok = requested <= stock
            checks.append(StockCheck(sku=sku, requested=requested, stock=stock, found=True, ok=ok).model_dump())
            if stock == 0 and requested > 0:
                flags.append(
                    ValidationFlag(
                        code="ZERO_STOCK",
                        severity="blocking",
                        message=f"{sku} has zero stock",
                        data={"sku": sku, "requested": requested, "stock": stock},
                    ).model_dump()
                )
            if requested > stock:
                flags.append(
                    ValidationFlag(
                        code="OVER_STOCK",
                        severity="blocking",
                        message=f"{sku} requested {requested} > stock {stock}",
                        data={"sku": sku, "requested": requested, "stock": stock},
                    ).model_dump()
                )
            unit_cost = row.get("unit_cost")
            priced = [i for i in inv.line_items if i.sku == sku and i.unit_price is not None]
            if unit_cost is not None and priced:
                for item in priced:
                    if abs(item.unit_price - float(unit_cost)) > 0.01:
                        flags.append(
                            ValidationFlag(
                                code="PRICE_DRIFT",
                                severity="warning",
                                message=f"{sku} unit price {item.unit_price} vs catalog {unit_cost}",
                                data={"sku": sku, "unit_price": item.unit_price, "unit_cost": unit_cost},
                            ).model_dump()
                        )
    finally:
        conn.close()
    return json.dumps({"flags": flags, "stock_checks": checks, "aggregates": aggregates})


def reconcile_totals(invoice_json: str | None = None, tolerance: float = 0.5) -> str:
    inv = parse_invoice(invoice_json)
    flags = []
    computed_lines = 0.0
    complete = True
    for item in inv.line_items:
        if item.unit_price is None:
            complete = False
            continue
        computed_lines += item.quantity * item.unit_price
    if complete and inv.subtotal is not None and abs(computed_lines - inv.subtotal) > tolerance:
        flags.append(
            ValidationFlag(
                code="TOTAL_MISMATCH",
                severity="blocking",
                message=f"Line sum {computed_lines:.2f} vs subtotal {inv.subtotal:.2f}",
                data={"line_sum": computed_lines, "subtotal": inv.subtotal},
            ).model_dump()
        )
    extras = (inv.tax or 0) + (inv.shipping or 0)
    expected_total = (inv.subtotal if inv.subtotal is not None else computed_lines) + extras
    if inv.total is not None and abs(expected_total - inv.total) > tolerance:
        flags.append(
            ValidationFlag(
                code="TOTAL_MISMATCH",
                severity="blocking",
                message=f"subtotal+tax+shipping {expected_total:.2f} vs total {inv.total:.2f}",
                data={"expected": expected_total, "total": inv.total},
            ).model_dump()
        )
    return json.dumps({"flags": flags, "line_sum": computed_lines, "expected_total": expected_total})


def check_duplicate(
    invoice_json: str | None = None,
    source_path: str = "",
    settings_json: str | None = None,
) -> str:
    inv = parse_invoice(invoice_json)
    source_path = source_path or current_payload().get("source_path") or ""
    settings = _settings(settings_json)
    canonical_hash = invoice_hash(inv)
    conn = _conn(settings)
    flags = []
    try:
        prior = storage.find_processed(conn, inv.invoice_number)
    finally:
        conn.close()
    for row in prior:
        if row.get("source_path") == (source_path or ""):
            continue
        if row.get("outcome") in {"DEDUP", "DUPLICATE"}:
            continue
        if row.get("canonical_hash") == canonical_hash:
            flags.append(
                ValidationFlag(
                    code="DEDUP",
                    severity="blocking",
                    message="Identical invoice already processed",
                    data={"prior": row.get("source_path"), "hash": canonical_hash},
                ).model_dump()
            )
            break
        prior_rev = row.get("revision") or ""
        if inv.revision:
            flags.append(
                ValidationFlag(
                    code="REVISION",
                    severity="warning",
                    message=f"Revision {inv.revision} of previously processed {inv.invoice_number}",
                    data={"prior": row.get("source_path")},
                ).model_dump()
            )
            break
        if not prior_rev:
            flags.append(
                ValidationFlag(
                    code="DUPLICATE",
                    severity="blocking",
                    message="Duplicate invoice number without revision",
                    data={"prior": row.get("source_path")},
                ).model_dump()
            )
            break
    if inv.revision and not any(f["code"] == "REVISION" for f in flags):
        flags.append(
            ValidationFlag(
                code="REVISION",
                severity="warning",
                message=f"Revision {inv.revision}",
                data={"revision": inv.revision},
            ).model_dump()
        )
    return json.dumps({"flags": flags, "canonical_hash": canonical_hash})


def scan_fraud_signals(invoice_json: str | None = None, raw_text: str = "") -> str:
    inv = parse_invoice(invoice_json)
    raw_text = raw_text or current_payload().get("raw_text") or ""
    flags = []
    blob = " ".join(inv.fraud_signals + [raw_text or ""])
    if inv.fraud_signals or any(
        token in blob.lower()
        for token in ["urgent", "wire transfer preferred", "pay immediately", "avoid penalties"]
    ):
        flags.append(
            ValidationFlag(
                code="FRAUD_LANGUAGE",
                severity="warning",
                message="Suspicious payment urgency or wire-change language",
                data={"signals": inv.fraud_signals},
            ).model_dump()
        )
    return json.dumps({"flags": flags})


def invoice_hash(invoice: Invoice) -> str:
    payload = {
        "invoice_number": invoice.invoice_number,
        "items": sorted((i.sku, float(i.quantity)) for i in invoice.line_items),
    }
    import hashlib

    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
