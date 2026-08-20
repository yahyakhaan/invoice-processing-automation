from __future__ import annotations

import json
from typing import Any

from invoice_agent.agents.base import SpecialistAgent, fn_tool
from invoice_agent.parsers import detect_format, parse_invoice_file
from invoice_agent.parsers.common import apply_ocr_fixes
from invoice_agent.parsers.pdf_parser import extract_pdf_text
from invoice_agent.parsers.text_parser import parse_text_invoice
from invoice_agent.schemas import InvoiceDraft


INGEST_PROMPT = """You are DocumentIngestionAgent.
Extract a faithful invoice draft from one source file.
Use only allowlisted tools. Prefer structured parsers for json/xml/csv.
For pdf, extract text then parse. For messy txt, apply OCR normalizers then parse.
Do not invent line items, amounts, or vendors that are not evidenced in the source.
Return unresolved_fields when vendor, invoice number, due date, line items, quantities, or total cannot be evidenced.
"""


class DocumentIngestionAgent(SpecialistAgent):
    agent_id = "ingestion"
    system_prompt = INGEST_PROMPT
    max_iterations = 6
    output_schema = InvoiceDraft

    def build_tools(self):
        return [
            fn_tool("detect_format", "Detect invoice file format", detect_format),
            fn_tool("parse_invoice", "Parse invoice file to draft JSON", self._parse),
            fn_tool("extract_pdf_text", "Extract text from a PDF", extract_pdf_text),
            fn_tool("parse_text_invoice", "Parse raw invoice text", self._parse_text),
            fn_tool("apply_ocr_normalizers", "Fix OCR letter-O digits and spacing", apply_ocr_fixes),
        ]

    def _parse(self, path: str) -> str:
        return parse_invoice_file(path).model_dump_json()

    def _parse_text(self, text: str) -> str:
        return parse_text_invoice(text, already_text=True).model_dump_json()

    def invoke_deterministic(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        trace: list[dict[str, Any]] = []
        path = payload.get("path")
        retry_errors = payload.get("retry_errors") or []
        if payload.get("raw_text") and not path:
            text = self.call_tool("apply_ocr_normalizers", {"text": payload["raw_text"]}, trace)
            draft_json = self.call_tool("parse_text_invoice", {"text": text}, trace)
            data = json.loads(draft_json)
            data["unresolved_fields"] = data.get("unresolved_fields") or []
            if retry_errors:
                data["evidence_spans"] = list(data.get("evidence_spans") or []) + ["retry"]
            return data, trace
        fmt = json.loads(self.call_tool("detect_format", {"path": path}, trace))
        if fmt["format"] == "pdf":
            text = self.call_tool("extract_pdf_text", {"path": path}, trace)
            text = self.call_tool("apply_ocr_normalizers", {"text": text}, trace)
            draft_json = self.call_tool("parse_text_invoice", {"text": text}, trace)
            data = json.loads(draft_json)
            data["source_format"] = "pdf"
            data["raw_text"] = text
            return data, trace
        draft_json = self.call_tool("parse_invoice", {"path": path}, trace)
        return json.loads(draft_json), trace

    def finalize_llm(self, payload: dict, trace: list[dict], structured: Any) -> dict[str, Any]:
        if structured is not None:
            data = structured.model_dump() if hasattr(structured, "model_dump") else dict(structured)
            if not data.get("line_items"):
                for item in reversed(trace):
                    result = item.get("result")
                    if isinstance(result, dict) and result.get("line_items"):
                        return result
            return data
        for item in reversed(trace):
            result = item.get("result")
            if isinstance(result, dict) and "line_items" in result:
                return result
        return {"unresolved_fields": ["ingest_failed"]}
