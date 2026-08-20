from invoice_agent.parsers import parse_invoice_file
from invoice_agent.parsers.text_parser import parse_text_invoice
from invoice_agent.services.normalization import normalize_draft


def test_json_nested_vendor(invoices):
    draft = parse_invoice_file(str(invoices / "invoice_1004.json"))
    assert draft.invoice_number == "INV-1004"
    assert draft.vendor_raw == "Precision Parts Ltd."
    assert len(draft.line_items) == 2


def test_json_keeps_nulls(invoices):
    draft = parse_invoice_file(str(invoices / "invoice_1009.json"))
    assert draft.vendor_raw == ""
    assert draft.due_date is None
    assert draft.line_items[0].quantity == -5
    assert draft.total == -250


def test_json_revision(invoices):
    draft = parse_invoice_file(str(invoices / "invoice_1004_revised.json"))
    assert draft.revision == "R1"


def test_xml_eur(invoices):
    draft = parse_invoice_file(str(invoices / "invoice_1014.xml"))
    assert draft.currency == "EUR"
    assert draft.total == 4125
    assert len(draft.line_items) == 2


def test_csv_pivoted(invoices):
    draft = parse_invoice_file(str(invoices / "invoice_1006.csv"))
    assert draft.invoice_number == "INV-1006"
    assert [i.sku for i in draft.line_items] == ["WidgetA", "WidgetB"]
    assert draft.line_items[0].quantity == 5


def test_csv_tabular_skips_totals(invoices):
    draft = parse_invoice_file(str(invoices / "invoice_1007.csv"))
    assert draft.invoice_number == "INV-1007"
    assert len(draft.line_items) == 3
    assert draft.total == 15525


def test_txt_clean(invoices):
    draft = parse_text_invoice(str(invoices / "invoice_1001.txt"))
    assert draft.invoice_number == "INV-1001"
    assert draft.vendor_raw.startswith("Widgets")
    assert len(draft.line_items) == 2
    assert draft.total == 5000


def test_txt_abbreviated(invoices):
    draft = parse_text_invoice(str(invoices / "invoice_1002.txt"))
    assert draft.invoice_number == "INV-1002"
    assert draft.line_items[0].sku == "GadgetX"
    assert draft.line_items[0].quantity == 20
    assert draft.total == 15000


def test_txt_email_and_unknown_items(invoices):
    draft = parse_text_invoice(str(invoices / "invoice_1008.txt"))
    assert draft.invoice_number == "INV-1008"
    names = [i.raw_name for i in draft.line_items]
    assert any("SuperGizmo" in n for n in names)
    assert any("MegaSprocket" in n for n in names)


def test_txt_ocr_and_aliases(invoices):
    draft = parse_text_invoice(str(invoices / "invoice_1012.txt"))
    inv = normalize_draft(draft)
    assert inv.invoice_number == "INV-1012"
    skus = [i.sku for i in inv.line_items]
    assert skus == ["WidgetA", "WidgetB", "GadgetX"]
    assert inv.vendor_identity_warning
    assert inv.vendor_canonical == "QuickShip"


def test_txt_aggregation_source(invoices):
    draft = parse_text_invoice(str(invoices / "invoice_1010.txt"))
    assert sum(i.quantity for i in draft.line_items if "WidgetA" in i.raw_name) == 12
