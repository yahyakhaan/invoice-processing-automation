from __future__ import annotations

from pathlib import Path

import pdfplumber

from invoice_agent.parsers.text_parser import parse_text_invoice
from invoice_agent.schemas import InvoiceDraft


def extract_pdf_text(path: str) -> str:
    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def parse_pdf_invoice(path: str | Path) -> InvoiceDraft:
    text = extract_pdf_text(str(path))
    draft = parse_text_invoice(text, already_text=True)
    draft.source_format = "pdf"
    draft.raw_text = text
    return draft
