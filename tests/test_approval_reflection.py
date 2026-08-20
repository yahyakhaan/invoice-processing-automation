from invoice_agent.agents.approval import ApprovalAgent
from invoice_agent.agents.critic import ApprovalCriticAgent
from invoice_agent.parsers import parse_invoice_file
from invoice_agent.schemas import ApprovalDecision, ValidationFlag, ValidationReport
from invoice_agent.services.normalization import normalize_draft


def test_critic_revises_illegal_approve(settings, invoices):
    invoice = normalize_draft(parse_invoice_file(str(invoices / "invoice_1002.txt")))
    validation = ValidationReport(
        flags=[
            ValidationFlag(
                code="OVER_STOCK",
                severity="blocking",
                message="over",
                data={},
            )
        ]
    )
    critic = ApprovalCriticAgent(settings)
    draft = ApprovalDecision(
        decision="approve",
        rationale="Looks fine",
        rule_hits=[],
        evidence_refs=[],
    )
    result = critic.invoke(
        {
            "approval_draft": draft.model_dump(),
            "validation": validation.model_dump(),
        }
    )
    assert result.output["verdict"] == "revise"
    assert result.agent_id == "critic"

    approval = ApprovalAgent(settings)
    revised = approval.invoke(
        {
            "invoice": invoice.model_dump(),
            "validation": validation.model_dump(),
            "critique": result.output,
        }
    )
    assert revised.output["decision"] == "reject"
    assert revised.output["revised_from"] == "critic"


def test_force_revise_flag(settings, invoices):
    invoice = normalize_draft(parse_invoice_file(str(invoices / "invoice_1001.txt")))
    validation = ValidationReport(flags=[])
    draft = ApprovalDecision(decision="approve", rationale="ok", rule_hits=[], evidence_refs=["rules"])
    critic = ApprovalCriticAgent(settings)
    result = critic.invoke(
        {
            "approval_draft": draft.model_dump(),
            "validation": validation.model_dump(),
            "force_revise": True,
        }
    )
    assert result.output["verdict"] == "revise"
