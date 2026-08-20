from __future__ import annotations

import json
from typing import Any

from invoice_agent.agents.base import SpecialistAgent, fn_tool
from invoice_agent.config import Settings
from invoice_agent.schemas import Invoice, MANDATORY_VALIDATION_TOOLS, ValidationReport, ValidationSynthesis
from invoice_agent.tools import integrity


VALIDATE_PROMPT = """You are ValidationAgent, a financial-control investigator.
You must call every mandatory check tool: check_required_fields, check_integrity,
lookup_inventory for each SKU, check_stock, reconcile_totals, check_duplicate, scan_fraud_signals.
When a tool asks for invoice_json, pass ONLY the invoice object (or omit the argument).
Never pass the full agent payload, skip_tools, or a wrapper like {"invoice": ...}.
You may reason about anomalies, but you must never invent stock quantities.
Copy flags from tool results. Do not override tool facts.
"""


class ValidationAgent(SpecialistAgent):
    agent_id = "validation"
    system_prompt = VALIDATE_PROMPT
    max_iterations = 10
    output_schema = ValidationSynthesis

    def build_tools(self):
        settings_json = self.settings.model_dump_json()

        def check_required_fields(invoice_json: str) -> str:
            return integrity.check_required_fields(invoice_json)

        def check_integrity(invoice_json: str) -> str:
            return integrity.check_integrity(invoice_json)

        def lookup_inventory(sku: str) -> str:
            return integrity.lookup_inventory(sku, settings_json)

        def check_stock(invoice_json: str) -> str:
            return integrity.check_stock(invoice_json, settings_json)

        def reconcile_totals(invoice_json: str) -> str:
            return integrity.reconcile_totals(invoice_json, self.settings.total_tolerance)

        def check_duplicate(invoice_json: str, source_path: str = "") -> str:
            return integrity.check_duplicate(invoice_json, source_path, settings_json)

        def scan_fraud_signals(invoice_json: str, raw_text: str = "") -> str:
            return integrity.scan_fraud_signals(invoice_json, raw_text)

        return [
            fn_tool("check_required_fields", "Flag missing vendor/number/due date/items/total", check_required_fields),
            fn_tool("check_integrity", "Flag negative quantities/totals and empty vendor", check_integrity),
            fn_tool("lookup_inventory", "Look up one SKU in SQLite inventory", lookup_inventory),
            fn_tool("check_stock", "Aggregate quantities and compare to stock", check_stock),
            fn_tool("reconcile_totals", "Compare line arithmetic to stated totals", reconcile_totals),
            fn_tool("check_duplicate", "Detect duplicate/revision/dedup against ledger", check_duplicate),
            fn_tool("scan_fraud_signals", "Scan for urgent-wire fraud language", scan_fraud_signals),
        ]

    def invoke_deterministic(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        skip = set(payload.get("skip_tools") or [])
        invoice = Invoice.model_validate(payload["invoice"])
        invoice_json = invoice.model_dump_json()
        source_path = payload.get("source_path") or ""
        raw_text = payload.get("raw_text") or ""
        extra = payload.get("extra_flags") or []
        flags: list[dict[str, Any]] = [
            f if isinstance(f, dict) else f.model_dump() for f in extra
        ]
        trace: list[dict[str, Any]] = []
        stock_checks = []
        aggregates = {}
        canonical_hash = None
        usd = None

        def run(name: str, args: dict) -> dict:
            if name in skip:
                return {}
            raw = self.call_tool(name, args, trace)
            return json.loads(raw) if raw else {}

        req = run("check_required_fields", {"invoice_json": invoice_json})
        flags.extend(req.get("flags") or [])
        integ = run("check_integrity", {"invoice_json": invoice_json})
        flags.extend(integ.get("flags") or [])
        for item in invoice.line_items:
            run("lookup_inventory", {"sku": item.sku})
        stock = run("check_stock", {"invoice_json": invoice_json})
        flags.extend(stock.get("flags") or [])
        stock_checks = stock.get("stock_checks") or []
        aggregates = stock.get("aggregates") or {}
        rec = run("reconcile_totals", {"invoice_json": invoice_json})
        flags.extend(rec.get("flags") or [])
        dup = run("check_duplicate", {"invoice_json": invoice_json, "source_path": source_path})
        flags.extend(dup.get("flags") or [])
        canonical_hash = dup.get("canonical_hash")
        fraud = run("scan_fraud_signals", {"invoice_json": invoice_json, "raw_text": raw_text})
        flags.extend(fraud.get("flags") or [])

        codes = sorted({f["code"] for f in flags})
        summary = "Validation complete. Flags: " + (", ".join(codes) if codes else "none")
        report = ValidationReport(
            flags=flags,
            stock_checks=stock_checks,
            aggregates=aggregates,
            usd_equivalent=usd,
            canonical_hash=canonical_hash,
            summary=summary,
            tools_called=[t["tool"] for t in trace],
        )
        return report.model_dump(), trace

    def finalize_llm(self, payload: dict, trace: list[dict], structured: Any) -> dict[str, Any]:
        flags: list[dict] = list(payload.get("extra_flags") or [])
        stock_checks = []
        aggregates = {}
        canonical_hash = None
        for item in trace:
            result = item.get("result")
            if not isinstance(result, dict):
                continue
            flags.extend(result.get("flags") or [])
            if result.get("stock_checks"):
                stock_checks = result["stock_checks"]
            if result.get("aggregates"):
                aggregates = result["aggregates"]
            if result.get("canonical_hash"):
                canonical_hash = result["canonical_hash"]
        summary = structured.summary if structured is not None else "Validation complete"
        report = ValidationReport(
            flags=flags,
            stock_checks=stock_checks,
            aggregates=aggregates,
            canonical_hash=canonical_hash,
            summary=summary,
            tools_called=[t["tool"] for t in trace],
        )
        return report.model_dump()
