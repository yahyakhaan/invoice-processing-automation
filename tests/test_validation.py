import json

from invoice_agent.parsers import parse_invoice_file
from invoice_agent.services.normalization import normalize_draft
from invoice_agent.tools import integrity


def test_over_stock_1002(settings, invoices):
    inv = normalize_draft(parse_invoice_file(str(invoices / "invoice_1002.txt")))
    result = json.loads(integrity.check_stock(inv.model_dump_json(), settings.model_dump_json()))
    codes = {f["code"] for f in result["flags"]}
    assert "OVER_STOCK" in codes


def test_unknown_1008(settings, invoices):
    inv = normalize_draft(parse_invoice_file(str(invoices / "invoice_1008.txt")))
    result = json.loads(integrity.check_stock(inv.model_dump_json(), settings.model_dump_json()))
    codes = {f["code"] for f in result["flags"]}
    assert "UNKNOWN_ITEM" in codes


def test_integrity_1009(settings, invoices):
    inv = normalize_draft(parse_invoice_file(str(invoices / "invoice_1009.json")))
    result = json.loads(integrity.check_integrity(inv.model_dump_json()))
    codes = {f["code"] for f in result["flags"]}
    assert "DATA_INTEGRITY" in codes


def test_total_mismatch_1013(settings, invoices):
    inv = normalize_draft(parse_invoice_file(str(invoices / "invoice_1013.json")))
    result = json.loads(integrity.reconcile_totals(inv.model_dump_json(), 0.5))
    codes = {f["code"] for f in result["flags"]}
    assert "TOTAL_MISMATCH" in codes
