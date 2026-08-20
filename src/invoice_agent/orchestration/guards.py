from __future__ import annotations

from invoice_agent.schemas import MANDATORY_VALIDATION_TOOLS, ValidationFlag, ValidationReport


def validation_tool_guard(tools_called: list[str]) -> list[str]:
    called = set(tools_called)
    missing = sorted(MANDATORY_VALIDATION_TOOLS - called)
    # lookup_inventory is covered if check_stock ran, but the contract still requires the lookup tool.
    return missing


def apply_guard(report: ValidationReport) -> ValidationReport:
    missing = validation_tool_guard(report.tools_called)
    if missing:
        report.flags.append(
            ValidationFlag(
                code="VALIDATION_INCOMPLETE",
                severity="blocking",
                message=f"Mandatory validation tools were not called: {', '.join(missing)}",
                data={"missing": missing},
            )
        )
    return report


def payment_eligible(
    *,
    blocking: bool,
    final_decision: str | None,
    human_decision: str | None,
    high_value: bool,
    fraud: bool,
) -> str:
    """Return pay | reject | pending."""
    if blocking:
        return "reject"
    if human_decision == "reject":
        return "reject"
    if human_decision == "approve" and not blocking:
        return "pay"
    if final_decision == "reject":
        return "reject"
    if high_value or fraud or final_decision == "escalate":
        return "pending"
    if final_decision == "approve":
        return "pay"
    return "reject"
