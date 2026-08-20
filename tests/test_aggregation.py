from invoice_agent.parsers import parse_invoice_file
from invoice_agent.services.normalization import aggregates, normalize_draft


def test_1010_aggregates_widget_a(invoices):
    inv = normalize_draft(parse_invoice_file(str(invoices / "invoice_1010.txt")))
    totals = aggregates(inv)
    assert totals["WidgetA"] == 12
    assert totals["WidgetB"] == 4
    assert totals["GadgetX"] == 2


def test_1013_aggregates_over_stock(invoices):
    inv = normalize_draft(parse_invoice_file(str(invoices / "invoice_1013.json")))
    totals = aggregates(inv)
    assert totals["WidgetA"] == 22
    assert totals["WidgetB"] == 18
    assert totals["GadgetX"] == 9
