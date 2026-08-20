from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

from invoice_agent.schemas import Invoice, ValidationReport

_PAYLOAD: ContextVar[dict[str, Any] | None] = ContextVar("agent_payload", default=None)


def bind_payload(payload: dict[str, Any]):
    return _PAYLOAD.set(payload)


def reset_payload(token) -> None:
    _PAYLOAD.reset(token)


def current_payload() -> dict[str, Any]:
    return _PAYLOAD.get() or {}


def _as_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return None
        return loaded if isinstance(loaded, dict) else None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return None


def _unwrap(data: dict[str, Any], nested_key: str) -> dict[str, Any]:
    nested = data.get(nested_key)
    if isinstance(nested, dict) and nested_key not in {"invoice_number", "vendor_raw"}:
        if nested_key == "invoice" and "invoice_number" not in data:
            return nested
        if nested_key == "validation" and "flags" not in data and "flags" in nested:
            return nested
        if nested_key == "approval_draft" and "decision" not in data and "decision" in nested:
            return nested
    return data


def parse_invoice(invoice_json: Any = None) -> Invoice:
    data = _as_dict(invoice_json)
    if data:
        data = _unwrap(data, "invoice")
        if "invoice_number" in data or "vendor_raw" in data or "line_items" in data:
            return Invoice.model_validate(data)
    fallback = current_payload().get("invoice")
    data = _as_dict(fallback)
    if not data:
        raise ValueError("No invoice available for tool call")
    return Invoice.model_validate(_unwrap(data, "invoice"))


def parse_validation(validation_json: Any = None) -> ValidationReport:
    data = _as_dict(validation_json)
    if data:
        data = _unwrap(data, "validation")
        if "flags" in data or "stock_checks" in data or "summary" in data:
            return ValidationReport.model_validate(data)
    fallback = current_payload().get("validation")
    data = _as_dict(fallback)
    if not data:
        raise ValueError("No validation report available for tool call")
    return ValidationReport.model_validate(_unwrap(data, "validation"))


def parse_json_object(value: Any, *fallback_keys: str) -> dict[str, Any]:
    data = _as_dict(value)
    if data and not set(fallback_keys) & {"invoice", "validation", "skip_tools"}:
        if "decision" in data or "verdict" in data:
            return data
    if data and "decision" in data:
        return data
    payload = current_payload()
    for key in fallback_keys:
        candidate = _as_dict(payload.get(key))
        if candidate:
            return candidate
    if data:
        return data
    raise ValueError(f"No JSON object available for keys {fallback_keys}")
