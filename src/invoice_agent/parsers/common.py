from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


OCR_LETTER_O = re.compile(r"(?<=\d)O(?=\d)|(?<=\.)O(?=\d)|O(?=\d{2,4})")


def apply_ocr_fixes(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"I\s+N\s+V\s+O\s+I\s+C\s+E", "INVOICE", text, flags=re.I)
    text = OCR_LETTER_O.sub("0", text)
    text = re.sub(r"\$(\d+),(\d{3})\.O(\d)", r"$\1,\2.0\3", text)
    return text


def parse_money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = apply_ocr_fixes(str(value)).strip()
    text = text.replace("$", "").replace(",", "").replace("€", "").strip()
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = apply_ocr_fixes(str(value)).strip()
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


_DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%b %d %Y",
    "%B %d %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
]


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = apply_ocr_fixes(str(value)).strip()
    if text.lower() in {"yesterday", "asap", "immediately", "n/a", "null", "none"}:
        return None
    text = text.replace(",", "")
    text = re.sub(r"\s+", " ", text)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def normalize_invoice_number(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    text = apply_ocr_fixes(str(value)).strip()
    text = re.sub(r"\s+", " ", text)
    match = re.search(r"(INV)[\s\-#]*(\d{3,})", text, flags=re.I)
    if match:
        return f"INV-{match.group(2)}"
    match = re.fullmatch(r"#?(\d{3,})", text)
    if match:
        return f"INV-{match.group(1)}"
    return text


def strip_email_envelope(text: str) -> str:
    lines = text.splitlines()
    i = 0
    headerish = True
    while i < len(lines) and headerish:
        line = lines[i].strip()
        if not line:
            i += 1
            if i < len(lines) and not re.match(r"^(from|to|subject|cc|date):", lines[i].strip(), re.I):
                headerish = False
            continue
        if re.match(r"^(from|to|subject|cc|date):", line, re.I):
            i += 1
            continue
        headerish = False
    body = "\n".join(lines[i:])
    body = re.sub(r"\n(best regards|thanks|thank you|sincerely)[\s\S]*$", "", body, flags=re.I)
    return body.strip()


def looks_like_total_label(value: str) -> bool:
    return bool(re.match(r"^(subtotal|tax|shipping|total|grand total)", value.strip(), re.I))
