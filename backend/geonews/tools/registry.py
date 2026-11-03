"""Tool registry: one definition per tool, used three ways.

1. In-process by the agent runtime (fast path, default).
2. Exported as MCP servers (geonews/mcp_servers) for Claude Code / Desktop / other agents.
3. Through an MCP client (agents/mcp_client.py) when GEONEWS_TOOLS_TRANSPORT=mcp.

Inputs are validated with Pydantic before execution: streaming requests use eager input
streaming, where the API does not validate tool inputs for us.
"""

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from geonews.telemetry import tracer

MAX_RESULT_CHARS = 12_000


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[Any], Awaitable[Any]]
    server: str  # which MCP server exports it: news | geo | memory

    def api_schema(self, eager: bool = False) -> dict:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        out = {"name": self.name, "description": self.description, "input_schema": schema}
        if eager:  # only meaningful on streaming requests
            out["eager_input_streaming"] = True
        return out


@dataclass
class ToolResult:
    content: str
    is_error: bool
    latency_ms: int


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, server: str, input_model: type[BaseModel]):
    def deco(fn):
        REGISTRY[name] = ToolSpec(name, (fn.__doc__ or "").strip(), input_model, fn, server)
        return fn

    return deco


def schemas(names: list[str], eager: bool = False) -> list[dict]:
    # Deterministic order keeps the tools prefix byte-stable for prompt caching.
    return [REGISTRY[n].api_schema(eager) for n in names]


async def execute(name: str, arguments: dict) -> ToolResult:
    start = time.perf_counter()
    spec = REGISTRY.get(name)
    with tracer.start_as_current_span(f"tool.{name}") as span:
        if spec is None:
            return ToolResult(f"Unknown tool: {name}", True, 0)
        try:
            args = spec.input_model.model_validate(arguments)
        except ValidationError as e:
            span.set_attribute("tool.error", "invalid_input")
            return ToolResult(f"INVALID_INPUT: {e.errors(include_url=False)}", True, 0)
        try:
            out = await spec.fn(args)
            content = out if isinstance(out, str) else json.dumps(out, default=str)
            is_error = False
        except Exception as e:  # surface tool failures to the model, don't crash the run
            content, is_error = f"Tool failed: {type(e).__name__}: {e}", True
        if len(content) > MAX_RESULT_CHARS:
            content = content[:MAX_RESULT_CHARS] + "\n...[truncated]"
        span.set_attribute("tool.is_error", is_error)
        return ToolResult(content, is_error, int((time.perf_counter() - start) * 1000))


def load_all() -> dict[str, ToolSpec]:
    from geonews.tools import geo, memory, news  # noqa: F401  (registers tools)

    return REGISTRY
