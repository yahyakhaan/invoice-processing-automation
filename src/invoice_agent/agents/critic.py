from __future__ import annotations

import json
from typing import Any

from invoice_agent.agents.base import SpecialistAgent, fn_tool
from invoice_agent.schemas import ApprovalCritique, ApprovalDecision, ValidationReport
from invoice_agent.tools.policy import get_policy, inspect_rule_trace


CRITIC_PROMPT = """You are ApprovalCriticAgent. You do not issue the final payment decision.
Independently challenge the ApprovalAgent draft.
Call get_policy and inspect_rule_trace.
If the draft approves or escalates through blocking flags, verdict=revise and require reject.
If rationale contradicts the rule trace, verdict=revise.
Otherwise verdict=accept.
Never mutate invoice line items or validation facts.
"""


class ApprovalCriticAgent(SpecialistAgent):
    agent_id = "critic"
    system_prompt = CRITIC_PROMPT
    max_iterations = 5
    output_schema = ApprovalCritique

    def build_tools(self):
        return [
            fn_tool("get_policy", "Fetch VP approval policy", get_policy),
            fn_tool(
                "inspect_rule_trace",
                "Compare approval draft against blocking validation flags",
                inspect_rule_trace,
            ),
        ]

    def invoke_deterministic(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        draft = ApprovalDecision.model_validate(payload["approval_draft"])
        validation = ValidationReport.model_validate(payload["validation"])
        trace: list[dict[str, Any]] = []
        self.call_tool("get_policy", {}, trace)
        inspect = json.loads(
            self.call_tool(
                "inspect_rule_trace",
                {
                    "validation_json": validation.model_dump_json(),
                    "approval_json": draft.model_dump_json(),
                },
                trace,
            )
        )
        issues = list(inspect.get("issues") or [])
        if payload.get("force_revise"):
            issues.append("Forced reflection test: rationale lacks evidence refs")
        if issues:
            critique = ApprovalCritique(
                verdict="revise",
                issues=issues,
                required_changes=["Align decision with blocking flags and cite rule_hits"],
                rationale="Critic found policy conflicts",
            )
        else:
            critique = ApprovalCritique(
                verdict="accept",
                issues=[],
                required_changes=[],
                rationale="Draft matches the deterministic control plane",
            )
        return critique.model_dump(), trace

    def finalize_llm(self, payload: dict, trace: list[dict], structured: Any) -> dict[str, Any]:
        issues = []
        for item in trace:
            result = item.get("result")
            if isinstance(result, dict):
                issues.extend(result.get("issues") or [])
        if structured is not None:
            data = structured.model_dump()
            if issues and data.get("verdict") == "accept":
                data["verdict"] = "revise"
                data["issues"] = issues
            return data
        return ApprovalCritique(
            verdict="revise" if issues else "accept",
            issues=issues,
            required_changes=["Fix policy conflict"] if issues else [],
            rationale="critic",
        ).model_dump()
