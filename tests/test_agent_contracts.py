from invoice_agent.agents.approval import ApprovalAgent
from invoice_agent.agents.critic import ApprovalCriticAgent
from invoice_agent.agents.ingestion import DocumentIngestionAgent
from invoice_agent.agents.validation import ValidationAgent


def test_agents_are_independently_invocable(settings, invoices):
    ingest = DocumentIngestionAgent(settings)
    result = ingest.invoke({"path": str(invoices / "invoice_1004.json")})
    assert result.agent_id == "ingestion"
    assert result.output["invoice_number"] == "INV-1004"
    assert "detect_format" in ingest.tool_names
    assert "mock_payment" not in ingest.tool_names


def test_distinct_prompts_and_toolsets(settings):
    ingest = DocumentIngestionAgent(settings)
    validation = ValidationAgent(settings)
    approval = ApprovalAgent(settings)
    critic = ApprovalCriticAgent(settings)
    prompts = {ingest.system_prompt, validation.system_prompt, approval.system_prompt, critic.system_prompt}
    assert len(prompts) == 4
    assert set(ingest.tool_names).isdisjoint(set(validation.tool_names))
    assert "evaluate_approval_rules" in approval.tool_names
    assert "inspect_rule_trace" in critic.tool_names
    assert "evaluate_approval_rules" not in critic.tool_names
    assert ingest.agent_id != critic.agent_id
