"""Tool registry: plain Python callables exposed to agents as OpenAI functions.

A `ToolBox` is the unit of access control. Each specialist agent receives exactly
one toolbox, so the Payment Agent physically cannot read delivery timestamps and
the Customer Agent cannot read payments -- the access boundary required by
README section 7 is enforced by construction, not by prompt wording.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolError(RuntimeError):
    """A tool rejected its arguments. Surfaced back to the model to retry."""


@dataclass
class ToolBox:
    domain: str
    tools: list[Tool] = field(default_factory=list)

    def add(self, tool: Tool) -> None:
        if any(t.name == tool.name for t in self.tools):
            raise ValueError(f"duplicate tool {tool.name!r} in domain {self.domain!r}")
        self.tools.append(tool)

    def names(self) -> list[str]:
        return [t.name for t in self.tools]

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self.tools]

    def invoke(self, name: str, arguments: str | dict[str, Any]) -> Any:
        """Run a tool by name. Never raises: errors come back as data the model can read."""
        tool = next((t for t in self.tools if t.name == name), None)
        if tool is None:
            return {
                "error": f"unknown tool {name!r} in domain {self.domain!r}",
                "available_tools": self.names(),
            }

        if isinstance(arguments, str):
            try:
                args = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                return {"error": f"arguments were not valid JSON: {exc}"}
        else:
            args = dict(arguments or {})

        required = tool.parameters.get("required", [])
        missing = [key for key in required if key not in args or args[key] in (None, "")]
        if missing:
            return {"error": f"missing required argument(s): {', '.join(missing)}"}

        allowed = set(tool.parameters.get("properties", {}))
        args = {k: v for k, v in args.items() if k in allowed}

        try:
            return tool.fn(**args)
        except ToolError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - agent must see the failure, not crash
            return {"error": f"{type(exc).__name__}: {exc}"}


def make_toolbox(domain: str, specs: list[tuple[Callable[..., Any], str, dict[str, Any]]]) -> ToolBox:
    box = ToolBox(domain=domain)
    for fn, description, parameters in specs:
        parameters.setdefault("type", "object")
        parameters.setdefault("properties", {})
        parameters.setdefault("additionalProperties", False)
        box.add(Tool(name=fn.__name__, description=description, parameters=parameters, fn=fn))
    return box


ORDER_ID_PARAM = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string", "description": "The 32-character Olist order_id under investigation."}
    },
    "required": ["order_id"],
    "additionalProperties": False,
}
