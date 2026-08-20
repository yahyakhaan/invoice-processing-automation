from __future__ import annotations

from pathlib import Path

from invoice_agent.parsers.csv_parser import parse_csv_invoice
from invoice_agent.parsers.json_parser import parse_json_invoice
from invoice_agent.parsers.pdf_parser import extract_pdf_text, parse_pdf_invoice
from invoice_agent.parsers.text_parser import parse_text_invoice
from invoice_agent.parsers.xml_parser import parse_xml_invoice
from invoice_agent.schemas import InvoiceDraft

STRUCTURED = {".json", ".xml", ".csv"}


def detect_format(path: str) -> dict:
    suffix = Path(path).suffix.lower()
    mapping = {".json": "json", ".xml": "xml", ".csv": "csv", ".pdf": "pdf", ".txt": "txt"}
    fmt = mapping.get(suffix, "txt")
    return {"format": fmt, "structured": suffix in STRUCTURED, "path": path}


def parse_invoice_file(path: str) -> InvoiceDraft:
    fmt = detect_format(path)["format"]
    if fmt == "json":
        return parse_json_invoice(path)
    if fmt == "xml":
        return parse_xml_invoice(path)
    if fmt == "csv":
        return parse_csv_invoice(path)
    if fmt == "pdf":
        return parse_pdf_invoice(path)
    return parse_text_invoice(path)
