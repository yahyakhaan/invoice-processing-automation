from invoice_agent.agents.validation import ValidationAgent
from invoice_agent.orchestration.guards import apply_guard, validation_tool_guard
from invoice_agent.parsers import parse_invoice_file
from invoice_agent.schemas import ValidationReport
from invoice_agent.services.normalization import normalize_draft


def test_skipping_mandatory_tool_fails_closed(settings, invoices):
    invoice = normalize_draft(parse_invoice_file(str(invoices / "invoice_1001.txt")))
    agent = ValidationAgent(settings)
    result = agent.invoke(
        {
            "invoice": invoice.model_dump(),
            "source_path": str(invoices / "invoice_1001.txt"),
            "skip_tools": ["check_stock"],
        }
    )
    missing = validation_tool_guard(result.output["tools_called"])
    assert "check_stock" in missing
    report = apply_guard(ValidationReport.model_validate(result.output))
    assert any(f.code == "VALIDATION_INCOMPLETE" for f in report.flags)


def test_full_validation_calls_mandatory_tools(settings, invoices):
    invoice = normalize_draft(parse_invoice_file(str(invoices / "invoice_1001.txt")))
    agent = ValidationAgent(settings)
    result = agent.invoke({"invoice": invoice.model_dump(), "source_path": "x"})
    assert validation_tool_guard(result.output["tools_called"]) == []
