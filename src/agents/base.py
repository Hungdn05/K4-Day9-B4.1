"""Specialist agent loop: native tool calling until the agent files its report.

Fidelity rule that shapes this whole module: an agent's report carries only its
*judgment* (booleans, chosen ids, a summary). Every number and timestamp in the
evidence card is copied verbatim from what the tools returned, never re-typed by
the model. An 8B model that transcribes `212.27` as `212.7` would silently cost
points, so it is never given the chance.

That split also gives the Verifier something real to check at CP4: the agent's
stated judgment can be cross-examined against the facts it was standing on.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from src import config
from src.llm.client import LLMClient, LLMError, LLMFatalError, shared_client
from src.tools.registry import ToolBox

REPORT_TOOL_NAME = "report_findings"


@dataclass
class ToolCallRecord:
    step: int
    tool: str
    arguments: dict[str, Any]
    ok: bool
    result_preview: str


@dataclass
class AgentResult:
    """The A2A message a specialist hands back to the supervisor."""

    agent: str
    domain: str
    status: str  # ok | incomplete | error
    facts: dict[str, Any] = field(default_factory=dict)
    findings: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    steps: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "domain": self.domain,
            "status": self.status,
            "facts": self.facts,
            "findings": self.findings,
            "steps": self.steps,
            "tools_used": [record.tool for record in self.tool_calls],
            "error": self.error,
        }

    def trace_dict(self) -> dict[str, Any]:
        payload = self.as_dict()
        payload["tool_calls"] = [asdict(record) for record in self.tool_calls]
        return payload


class SpecialistAgent:
    """One LLM, one system prompt, one toolbox."""

    def __init__(
        self,
        name: str,
        toolbox: ToolBox,
        system_prompt: str,
        report_schema: dict[str, Any],
        required_tools: tuple[str, ...] = (),
        reasoning: bool = False,
        client: LLMClient | None = None,
    ) -> None:
        self.name = name
        self.toolbox = toolbox
        self.system_prompt = system_prompt
        self.report_schema = report_schema
        self.required_tools = required_tools
        self.reasoning = reasoning
        self.client = client or shared_client()

    # --- tool schema exposed to the model ------------------------------------
    def _report_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": REPORT_TOOL_NAME,
                "description": (
                    "File your findings and end your turn. Call this only after you have "
                    "read every data tool you need. Report judgments only -- do not "
                    "restate numbers or timestamps, they are carried automatically."
                ),
                "parameters": self.report_schema,
            },
        }

    # --- main loop -----------------------------------------------------------
    def run(self, task: str, max_steps: int = config.SPECIALIST_MAX_STEPS) -> AgentResult:
        result = AgentResult(agent=self.name, domain=self.toolbox.domain, status="incomplete")
        tools = self.toolbox.schemas() + [self._report_tool()]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]

        for step in range(1, max_steps + 1):
            result.steps = step
            try:
                message = self.client.chat(messages, tools=tools, reasoning=self.reasoning)
            except LLMFatalError:
                raise  # billing/auth: abort the run rather than file an empty card
            except LLMError as exc:
                result.status = "error"
                result.error = str(exc)
                return result

            messages.append(_assistant_message(message))
            calls = message.get("tool_calls") or []

            if not calls:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Do not answer in prose. Either call a data tool or call "
                            f"{REPORT_TOOL_NAME} to finish."
                        ),
                    }
                )
                continue

            for call in calls:
                fn = call.get("function", {})
                tool_name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")

                if tool_name == REPORT_TOOL_NAME:
                    payload, error = self._validate_report(raw_args, result)
                    if error is None:
                        result.findings = payload
                        result.status = "ok"
                        result.tool_calls.append(
                            ToolCallRecord(step, tool_name, payload, True, "filed")
                        )
                        return result
                    result.tool_calls.append(
                        ToolCallRecord(step, tool_name, {}, False, error)
                    )
                    messages.append(_tool_message(call, {"error": error}))
                    continue

                output = self.toolbox.invoke(tool_name, raw_args)
                ok = not (isinstance(output, dict) and "error" in output)
                if ok:
                    result.facts[tool_name] = output
                result.tool_calls.append(
                    ToolCallRecord(
                        step,
                        tool_name,
                        _safe_args(raw_args),
                        ok,
                        json.dumps(output, ensure_ascii=False)[:220],
                    )
                )
                messages.append(_tool_message(call, output))

        result.error = f"did not file findings within {max_steps} steps"
        return result

    # --- report validation ---------------------------------------------------
    def _validate_report(
        self, raw_args: str | dict[str, Any], result: AgentResult
    ) -> tuple[dict[str, Any], str | None]:
        if isinstance(raw_args, str):
            try:
                payload = json.loads(raw_args or "{}")
            except json.JSONDecodeError as exc:
                return {}, f"arguments were not valid JSON: {exc}"
        else:
            payload = dict(raw_args or {})

        missing_tools = [name for name in self.required_tools if name not in result.facts]
        if missing_tools:
            return {}, (
                "You have not read the data yet. Call these tools successfully before "
                f"reporting: {', '.join(missing_tools)}"
            )

        missing_fields = [
            key for key in self.report_schema.get("required", []) if key not in payload
        ]
        if missing_fields:
            return {}, f"missing required field(s): {', '.join(missing_fields)}"

        allowed = set(self.report_schema.get("properties", {}))
        return {k: v for k, v in payload.items() if k in allowed}, None


# --- message helpers ---------------------------------------------------------
def _assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    """Strip provider extras so the message can be echoed back safely."""
    out: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("tool_calls"):
        out["tool_calls"] = [
            {
                "id": call.get("id"),
                "type": "function",
                "function": {
                    "name": call.get("function", {}).get("name"),
                    "arguments": call.get("function", {}).get("arguments", "{}"),
                },
            }
            for call in message["tool_calls"]
        ]
    return out


def _tool_message(call: dict[str, Any], output: Any) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.get("id"),
        "name": call.get("function", {}).get("name"),
        "content": json.dumps(output, ensure_ascii=False, default=str),
    }


def _safe_args(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": str(raw)[:200]}
