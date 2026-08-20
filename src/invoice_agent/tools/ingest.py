from __future__ import annotations

import json
from typing import Any

from invoice_agent.parsers import detect_format, parse_invoice_file
from invoice_agent.parsers.common import apply_ocr_fixes
from invoice_agent.parsers.pdf_parser import extract_pdf_text
from invoice_agent.parsers.text_parser import parse_text_invoice


def tool_detect_format(path: str) -> str:
    return json.dumps(detect_format(path))


def tool_parse_invoice(path: str) -> str:
    draft = parse_invoice_file(path)
    return draft.model_dump_json()


def tool_extract_pdf_text(path: str) -> str:
    return extract_pdf_text(path)


def tool_parse_text(text: str) -> str:
    return parse_text_invoice(apply_ocr_fixes(text), already_text=True).model_dump_json()


def tool_ocr_normalize(text: str) -> str:
    return apply_ocr_fixes(text)


INGEST_TOOL_FUNCS: dict[str, Any] = {
    "detect_format": tool_detect_format,
    "parse_invoice": tool_parse_invoice,
    "extract_pdf_text": tool_extract_pdf_text,
    "parse_text_invoice": tool_parse_text,
    "apply_ocr_normalizers": tool_ocr_normalize,
}
