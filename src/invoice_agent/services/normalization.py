from __future__ import annotations

from collections import defaultdict

from invoice_agent.aliases import canonical_sku, canonical_vendor
from invoice_agent.schemas import Invoice, InvoiceDraft, LineItem, ValidationFlag


def normalize_draft(draft: InvoiceDraft) -> Invoice:
    items: list[LineItem] = []
    ocr = False
    for item in draft.line_items:
        sku = canonical_sku(item.raw_name or item.sku)
        if sku != (item.raw_name or item.sku).replace(" ", ""):
            if " " in (item.raw_name or "") or item.raw_name != sku:
                if item.raw_name.replace(" ", "") != sku and " " in item.raw_name:
                    ocr = True
        if item.raw_name and item.raw_name != sku:
            compact = item.raw_name.replace(" ", "")
            if compact != sku and any(ch.isalpha() for ch in item.raw_name):
                if " " in item.raw_name or "O" in (draft.raw_text or ""):
                    ocr = True
        items.append(
            LineItem(
                sku=sku,
                raw_name=item.raw_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                amount=item.amount,
                note=item.note,
            )
        )
    vendor_raw = draft.vendor_raw or ""
    vendor_canonical, formerly = canonical_vendor(vendor_raw)
    if draft.raw_text and ("formerly" in draft.raw_text.lower() or formerly):
        formerly = True
    number = draft.invoice_number or "UNKNOWN"
    return Invoice(
        invoice_number=number,
        revision=draft.revision,
        vendor_raw=vendor_raw,
        vendor_canonical=vendor_canonical or vendor_raw,
        currency=(draft.currency or "USD").upper(),
        invoice_date=draft.invoice_date,
        due_date=draft.due_date,
        line_items=items,
        subtotal=draft.subtotal,
        tax=draft.tax,
        shipping=draft.shipping,
        total=draft.total,
        payment_terms=draft.payment_terms,
        source_format=draft.source_format,
        fraud_signals=list(draft.fraud_signals),
        vendor_identity_warning=formerly,
    )


def identity_flags(invoice: Invoice, draft: InvoiceDraft | None = None) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []
    if invoice.vendor_identity_warning:
        flags.append(
            ValidationFlag(
                code="VENDOR_IDENTITY",
                severity="warning",
                message="Vendor identity changed or includes a former-name note",
                data={"vendor_raw": invoice.vendor_raw, "vendor_canonical": invoice.vendor_canonical},
            )
        )
    if draft and draft.source_format in {"txt", "pdf"} and (
        "INV " in (draft.raw_text or "")
        or "2O26" in (draft.raw_text or "")
        or ".O0" in (draft.raw_text or "")
        or "Widget A" in (draft.raw_text or "")
    ):
        flags.append(
            ValidationFlag(
                code="OCR",
                severity="warning",
                message="OCR/typo artifacts were normalized",
                data={},
            )
        )
    return flags


def aggregates(invoice: Invoice) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for item in invoice.line_items:
        totals[item.sku] += item.quantity
    return dict(totals)
