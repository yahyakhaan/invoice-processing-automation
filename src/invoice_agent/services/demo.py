from __future__ import annotations

from datetime import date

from invoice_agent.schemas import Invoice, LineItem


def hitl_demo_invoice(thread_id: str) -> Invoice:
    short = thread_id.replace("-", "")[:8]
    return Invoice(
        invoice_number=f"DEMO-HITL-{short}",
        vendor_raw="Widgets Inc.",
        vendor_canonical="Widgets Inc.",
        currency="USD",
        invoice_date=date(2026, 2, 1),
        due_date=date(2026, 3, 1),
        line_items=[
            LineItem(sku="WidgetA", raw_name="WidgetA", quantity=15, unit_price=250.0, amount=3750.0),
            LineItem(sku="WidgetB", raw_name="WidgetB", quantity=10, unit_price=500.0, amount=5000.0),
            LineItem(sku="GadgetX", raw_name="GadgetX", quantity=5, unit_price=750.0, amount=3750.0),
        ],
        subtotal=12500.0,
        tax=0.0,
        shipping=0.0,
        total=12500.0,
        payment_terms="Net 30",
        source_format="demo",
        fraud_signals=[],
    )
