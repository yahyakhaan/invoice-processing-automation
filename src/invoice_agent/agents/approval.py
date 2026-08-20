from __future__ import annotations

import json
from typing import Any

from invoice_agent.agents.base import SpecialistAgent, fn_tool
from invoice_agent.schemas import ApprovalDecision, Invoice, ValidationReport
from invoice_agent.tools.policy import convert_fx, evaluate_approval_rules, get_policy


APPROVE_PROMPT = """You are ApprovalAgent, simulating VP-level commercial judgment.
You MUST call evaluate_approval_rules, convert_fx, and get_policy.
Pass invoice_json as the invoice object only, and validation_json as the validation report only.
You may explain risk, but you cannot override blocking validation flags.
If the critic sends revision notes, adjust rationale and decision only within policy.
High-value invoices must escalate, not auto-approve.
"""


class ApprovalAgent(SpecialistAgent):
    agent_id = "approval"
    system_prompt = APPROVE_PROMPT
    max_iterations = 6
    output_schema = ApprovalDecision

    def build_tools(self):
        settings_json = self.settings.model_dump_json()

        def _fx(invoice_json: str) -> str:
            return convert_fx(invoice_json, settings_json)

        def _rules(invoice_json: str, validation_json: str) -> str:
            return evaluate_approval_rules(invoice_json, validation_json, settings_json)

        return [
            fn_tool("convert_fx", "Convert invoice total to USD using local FX table", _fx),
            fn_tool("get_policy", "Fetch VP approval policy", get_policy),
            fn_tool("evaluate_approval_rules", "Deterministic approve/reject/escalate rules", _rules),
        ]

    def invoke_deterministic(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        invoice = Invoice.model_validate(payload["invoice"])
        validation = ValidationReport.model_validate(payload["validation"])
        trace: list[dict[str, Any]] = []
        self.call_tool("get_policy", {}, trace)
        fx = json.loads(self.call_tool("convert_fx", {"invoice_json": invoice.model_dump_json()}, trace))
        for flag in fx.get("flags") or []:
            from invoice_agent.schemas import ValidationFlag

            obj = ValidationFlag.model_validate(flag)
            if obj.code not in {f.code for f in validation.flags}:
                validation.flags.append(obj)
        validation.usd_equivalent = fx.get("usd_equivalent")
        rules = json.loads(
            self.call_tool(
                "evaluate_approval_rules",
                {
                    "invoice_json": invoice.model_dump_json(),
                    "validation_json": validation.model_dump_json(),
                },
                trace,
            )
        )
        critique = payload.get("critique")
        rationale = rules["rationale"]
        decision = rules["decision"]
        if critique and critique.get("verdict") == "revise":
            rationale = rationale + " Revised after critic: " + "; ".join(critique.get("required_changes") or [])
        decision_obj = ApprovalDecision(
            decision=decision,
            rationale=rationale,
            risk_score=0.8 if decision != "approve" else 0.15,
            rule_hits=rules.get("rule_hits") or [],
            evidence_refs=["evaluate_approval_rules", "convert_fx"],
            revised_from="critic" if critique else None,
        )
        output = decision_obj.model_dump()
        output["_fx"] = fx
        output["_validation_flags"] = [f.model_dump() if hasattr(f, "model_dump") else f for f in validation.flags]
        output["_usd"] = fx.get("usd_equivalent")
        return output, trace

    def finalize_llm(self, payload: dict, trace: list[dict], structured: Any) -> dict[str, Any]:
        rules = {}
        fx = {}
        for item in trace:
            if item.get("tool") == "evaluate_approval_rules" and isinstance(item.get("result"), dict):
                rules = item["result"]
            if item.get("tool") == "convert_fx" and isinstance(item.get("result"), dict):
                fx = item["result"]
        if structured is None:
            structured = ApprovalDecision(
                decision=rules.get("decision", "reject"),
                rationale=rules.get("rationale", "No structured approval output"),
                rule_hits=rules.get("rule_hits") or [],
            )
        data = structured.model_dump() if hasattr(structured, "model_dump") else dict(structured)
        if rules.get("decision") == "reject":
            data["decision"] = "reject"
            data["rule_hits"] = rules.get("rule_hits") or data.get("rule_hits")
        data["_fx"] = fx
        data["_usd"] = fx.get("usd_equivalent")
        return data
