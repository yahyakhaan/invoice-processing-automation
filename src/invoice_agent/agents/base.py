from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from invoice_agent.coerce import bind_payload, reset_payload
from invoice_agent.config import Settings
from invoice_agent.llm import get_chat_model
from invoice_agent.observability.console import progress
from invoice_agent.schemas import AgentResult


class SpecialistAgent(ABC):
    agent_id: str
    system_prompt: str
    max_iterations: int = 6
    output_schema: type | None = None

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = get_chat_model(settings)
        self.tools = self.build_tools()
        self.tool_map = {t.name: t for t in self.tools}

    @abstractmethod
    def build_tools(self) -> list[StructuredTool]:
        ...

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    def invoke(self, payload: dict[str, Any]) -> AgentResult:
        started = time.perf_counter()
        token = bind_payload(payload)
        try:
            if not self.settings.agentic_mode:
                output, trace = self.invoke_deterministic(payload)
                return AgentResult(
                    agent_id=self.agent_id,
                    output=output,
                    tool_trace=trace,
                    messages=[{"role": "system", "content": self.system_prompt[:200]}],
                    model_calls=0,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            output, trace, messages, calls = self.invoke_llm(payload)
            return AgentResult(
                agent_id=self.agent_id,
                output=output,
                tool_trace=trace,
                messages=messages,
                model_calls=calls,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        finally:
            reset_payload(token)

    @abstractmethod
    def invoke_deterministic(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        ...

    def invoke_llm(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict], list[dict], int]:
        llm = self.model.bind_tools(self.tools)
        user = json.dumps(payload, default=str)
        messages = [SystemMessage(content=self.system_prompt), HumanMessage(content=user)]
        trace: list[dict[str, Any]] = []
        calls = 0
        progress(
            f"{self.agent_id}: starting LLM loop",
            detail=f"tools={', '.join(self.tool_names) or 'none'}",
        )
        for iteration in range(self.max_iterations):
            progress(
                f"{self.agent_id}: waiting on model",
                detail=f"iteration {iteration + 1}/{self.max_iterations}",
            )
            response = llm.invoke(messages)
            calls += 1
            messages.append(response)
            tool_calls = getattr(response, "tool_calls", None) or []
            content = (getattr(response, "content", None) or "").strip()
            if content and not tool_calls:
                progress(f"{self.agent_id}: model reply", detail=_clip_text(content, 160))
            if not tool_calls:
                break
            for call in tool_calls:
                name = call.get("name")
                args = call.get("args") or {}
                progress(f"{self.agent_id}: tool → {name}", detail=_tool_arg_summary(args))
                tool = self.tool_map.get(name)
                if tool is None:
                    result = json.dumps({"error": f"tool {name} not allowed"})
                else:
                    args = _normalize_tool_args(args, payload)
                    try:
                        result = tool.invoke(args)
                    except Exception as exc:  # noqa: BLE001
                        try:
                            result = tool.invoke({})
                        except Exception:
                            result = json.dumps({"error": str(exc), "tool": name})
                progress(f"{self.agent_id}: tool ← {name}", detail=_clip_text(str(result), 120))
                trace.append({"tool": name, "args": args, "result": _clip(result)})
                messages.append(
                    ToolMessage(content=str(result), tool_call_id=call.get("id") or name)
                )
        structured = None
        if self.output_schema is not None:
            try:
                progress(f"{self.agent_id}: structuring final output")
                structured_llm = self.model.with_structured_output(self.output_schema)
                structured = structured_llm.invoke(messages)
                calls += 1
            except Exception as exc:  # noqa: BLE001
                structured = None
                trace.append({"tool": "structured_output", "args": {}, "result": {"error": str(exc)}})
        output = self.finalize_llm(payload, trace, structured)
        progress(f"{self.agent_id}: done", detail=f"model_calls={calls} tools={len(trace)}")
        dump = []
        for msg in messages:
            dump.append({"type": msg.__class__.__name__, "content": getattr(msg, "content", "")[:500]})
        return output, trace, dump, calls

    def finalize_llm(self, payload: dict, trace: list[dict], structured: Any) -> dict[str, Any]:
        if structured is None:
            return {"trace": trace}
        if hasattr(structured, "model_dump"):
            return structured.model_dump()
        return dict(structured)

    def call_tool(self, name: str, args: dict[str, Any], trace: list[dict[str, Any]]) -> str:
        tool = self.tool_map[name]
        result = tool.invoke(args)
        trace.append({"tool": name, "args": args, "result": _clip(result)})
        return result if isinstance(result, str) else json.dumps(result)


def _normalize_tool_args(args: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args)
    invoice = payload.get("invoice")
    if invoice is not None:
        dumped = json.dumps(invoice, default=str)
        raw = normalized.get("invoice_json")
        if raw is None or (isinstance(raw, dict) and "invoice_number" not in raw):
            if isinstance(raw, dict) and isinstance(raw.get("invoice"), dict):
                normalized["invoice_json"] = json.dumps(raw["invoice"], default=str)
            else:
                normalized["invoice_json"] = dumped
        elif isinstance(raw, dict):
            normalized["invoice_json"] = json.dumps(raw, default=str)
    validation = payload.get("validation")
    if validation is not None:
        dumped = json.dumps(validation, default=str)
        raw = normalized.get("validation_json")
        if raw is None or (isinstance(raw, dict) and "flags" not in raw):
            normalized["validation_json"] = dumped
        elif isinstance(raw, dict):
            normalized["validation_json"] = json.dumps(raw, default=str)
    draft = payload.get("approval_draft") or payload.get("approval_final")
    if draft is not None and not normalized.get("approval_json"):
        normalized["approval_json"] = json.dumps(draft, default=str)
    if payload.get("source_path") and not normalized.get("source_path"):
        normalized["source_path"] = payload["source_path"]
    if payload.get("raw_text") and not normalized.get("raw_text"):
        normalized["raw_text"] = payload["raw_text"]
    return normalized


def _clip(result: Any, limit: int = 1500) -> Any:
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    if len(text) > limit:
        return text[:limit] + "…"
    try:
        return json.loads(text)
    except Exception:
        return text


def _clip_text(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _tool_arg_summary(args: dict[str, Any]) -> str:
    keys = [k for k in args.keys() if k not in {"invoice_json", "validation_json", "approval_json"}]
    if not keys:
        return "payload args"
    parts = []
    for key in keys[:4]:
        value = args.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            parts.append(f"{key}={value}")
        else:
            parts.append(key)
    return ", ".join(parts)


def fn_tool(name: str, description: str, func: Callable) -> StructuredTool:
    return StructuredTool.from_function(func=func, name=name, description=description)
