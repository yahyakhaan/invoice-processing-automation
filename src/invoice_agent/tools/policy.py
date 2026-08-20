from __future__ import annotations

import json
from pathlib import Path

from invoice_agent import storage
from invoice_agent.coerce import parse_invoice, parse_json_object, parse_validation
from invoice_agent.config import Settings
from invoice_agent.schemas import Invoice, ValidationReport


POLICY_TEXT = """
VP invoice policy:
- Never pay if blocking validation flags are present.
- Human VP sign-off is required when USD-equivalent amount is greater than $10,000.
- Human review is required for FRAUD_LANGUAGE when there is no blocking inventory/integrity failure.
- Non-USD currency always attaches FX_REVIEW; convert with the local FX table before the $10k check.
- LLM and humans cannot override blocking validation facts.
"""


def convert_fx(invoice_json: str | None = None, settings_json: str | None = None) -> str:
    inv = parse_invoice(invoice_json)
    settings = Settings.model_validate_json(settings_json) if settings_json else Settings()
    amount = inv.total if inv.total is not None else inv.subtotal or 0.0
    conn = storage.connect(Path(settings.inventory_db))
    try:
        rate = storage.fx_rate(conn, inv.currency or "USD", settings.fx_eur_usd)
    finally:
        conn.close()
    usd = amount * rate
    flags = []
    if (inv.currency or "USD").upper() != "USD":
        flags.append(
            {
                "code": "FX_REVIEW",
                "severity": "warning",
                "message": f"{inv.currency} converted at {rate} → ${usd:.2f} USD",
                "data": {"currency": inv.currency, "rate": rate, "usd": usd},
            }
        )
    return json.dumps({"usd_equivalent": usd, "rate": rate, "flags": flags})


def get_policy() -> str:
    return json.dumps(
        {
            "high_value_usd": 10000,
            "no_override_blocking": True,
            "fraud_requires_human": True,
            "text": POLICY_TEXT.strip(),
        }
    )


def evaluate_approval_rules(
    invoice_json: str | None = None,
    validation_json: str | None = None,
    settings_json: str | None = None,
) -> str:
    inv = parse_invoice(invoice_json)
    report = parse_validation(validation_json)
    settings = Settings.model_validate_json(settings_json) if settings_json else Settings()
    hits: list[str] = []
    usd = report.usd_equivalent
    if usd is None:
        fx = json.loads(convert_fx(invoice_json, settings_json))
        usd = fx["usd_equivalent"]
        from invoice_agent.schemas import ValidationFlag as _Flag

        report.flags.extend(_Flag.model_validate(f) for f in fx.get("flags") or [])
    blocking = [f.code for f in report.flags if f.severity == "blocking"]
    if "HIGH_VALUE" not in hits and usd is not None and usd > settings.high_value_usd:
        hits.append("HIGH_VALUE")
    if any(f.code == "FRAUD_LANGUAGE" for f in report.flags):
        hits.append("FRAUD_LANGUAGE")
    if any(f.code == "FX_REVIEW" for f in report.flags):
        hits.append("FX_REVIEW")
    if blocking:
        hits.extend(blocking)
        return json.dumps(
            {
                "decision": "reject",
                "rule_hits": hits,
                "usd_equivalent": usd,
                "rationale": "Blocking validation flags forbid payment: " + ", ".join(blocking),
            }
        )
    if usd is not None and usd > settings.high_value_usd:
        return json.dumps(
            {
                "decision": "escalate",
                "rule_hits": hits,
                "usd_equivalent": usd,
                "rationale": f"USD-equivalent ${usd:.2f} exceeds ${settings.high_value_usd:.0f} VP threshold",
            }
        )
    if "FRAUD_LANGUAGE" in hits:
        return json.dumps(
            {
                "decision": "escalate",
                "rule_hits": hits,
                "usd_equivalent": usd,
                "rationale": "Fraud language requires VP review; automatic payment is blocked",
            }
        )
    return json.dumps(
        {
            "decision": "approve",
            "rule_hits": hits,
            "usd_equivalent": usd,
            "rationale": f"No blocking flags; {inv.vendor_canonical} invoice {inv.invoice_number} is within policy",
        }
    )


def inspect_rule_trace(validation_json: str | None = None, approval_json: str | None = None) -> str:
    report = parse_validation(validation_json)
    approval = parse_json_object(approval_json, "approval_draft", "approval_final")
    blocking = [f.code for f in report.flags if f.severity == "blocking"]
    decision = approval.get("decision")
    issues = []
    if blocking and decision == "approve":
        issues.append("Approval cannot approve through blocking flags")
    if blocking and decision == "escalate":
        issues.append("Escalation cannot bypass blocking flags; must reject")
    return json.dumps({"blocking": blocking, "issues": issues, "decision": decision})
