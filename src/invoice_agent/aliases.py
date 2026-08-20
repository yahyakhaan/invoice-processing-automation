from __future__ import annotations

import re

SKU_ALIASES = {
    "widgeta": "WidgetA",
    "widget a": "WidgetA",
    "widget-a": "WidgetA",
    "widgetb": "WidgetB",
    "widget b": "WidgetB",
    "widget-b": "WidgetB",
    "gadgetx": "GadgetX",
    "gadget x": "GadgetX",
    "gadget-x": "GadgetX",
    "fakeitem": "FakeItem",
    "fake item": "FakeItem",
}

VENDOR_ALIASES = {
    "quickship distributers": "QuickShip",
    "quickship distributors": "QuickShip",
    "quickship": "QuickShip",
    "fastship ltd": "QuickShip",
    "fastship ltd.": "QuickShip",
    "fastship": "QuickShip",
    "widgets inc": "Widgets Inc.",
    "widgets inc.": "Widgets Inc.",
}


def _key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return re.sub(r"\s+", " ", cleaned)


def canonical_sku(raw_name: str) -> str:
    note_stripped = re.sub(r"\([^)]*\)", "", raw_name).strip()
    key = _key(note_stripped)
    collapsed = key.replace(" ", "")
    if key in SKU_ALIASES:
        return SKU_ALIASES[key]
    if collapsed in SKU_ALIASES:
        return SKU_ALIASES[collapsed]
    if not note_stripped:
        return raw_name.strip()
    parts = re.findall(r"[A-Za-z0-9]+", note_stripped)
    if len(parts) == 1:
        token = parts[0]
        collapsed = token.lower()
        return SKU_ALIASES.get(collapsed, token)
    return "".join(p.capitalize() if not p.isupper() else p for p in parts) or raw_name.strip()


def canonical_vendor(raw: str) -> tuple[str, bool]:
    if not raw:
        return "", False
    formerly = "formerly" in raw.lower()
    primary = re.split(r"\(|formerly", raw, maxsplit=1, flags=re.I)[0].strip(" ,-")
    key = _key(primary)
    mapped = VENDOR_ALIASES.get(key, primary)
    return mapped, formerly
