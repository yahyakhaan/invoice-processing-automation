import json

from invoice_agent.coerce import bind_payload, parse_invoice, reset_payload
from invoice_agent.tools.integrity import check_required_fields


def test_parse_invoice_unwraps_agent_payload():
    invoice = {
        "invoice_number": "INV-1012",
        "vendor_raw": "QuickShip Distributers",
        "vendor_canonical": "QuickShip",
        "line_items": [{"sku": "WidgetA", "raw_name": "Widget A", "quantity": 12, "unit_price": 250}],
    }
    wrapped = {"invoice": invoice, "skip_tools": None, "source_path": "x"}
    parsed = parse_invoice(wrapped)
    assert parsed.invoice_number == "INV-1012"
    parsed_json = parse_invoice(json.dumps(wrapped))
    assert parsed_json.vendor_canonical == "QuickShip"


def test_check_required_fields_accepts_wrapped_payload():
    invoice = {
        "invoice_number": "INV-1012",
        "vendor_raw": "QuickShip",
        "vendor_canonical": "QuickShip",
        "due_date": "2026-02-25",
        "total": 9975.0,
        "line_items": [{"sku": "WidgetA", "raw_name": "WidgetA", "quantity": 1}],
    }
    token = bind_payload({"invoice": invoice})
    try:
        raw = check_required_fields(json.dumps({"invoice": invoice, "skip_tools": None}))
        data = json.loads(raw)
        assert data["missing"] == []
    finally:
        reset_payload(token)
