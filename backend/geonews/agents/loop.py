"""A small, explicit agent loop shared by the orchestrator and subagents.

Kept hand-written (rather than the SDK tool runner) so every policy is visible in one place:
step and token budgets, parallel tool execution, append-only history (required for thinking
blocks to stay valid), refusal / truncation handling, and per-call trace records.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from geonews.db import session_scope
from geonews.llm.router import get_router
from geonews.models import ToolCall
from geonews.tools import registry

log = logging.getLogger(__name__)
CITE_ID = re.compile(r"\bA\d+\b")

Emit = Callable[[str, dict], Awaitable[None]]


async def _no_emit(kind: str, data: dict) -> None:
    pass


@dataclass
class LocalTool:
    """A tool implemented by the agent itself (delegation, skills, final-answer submission)."""

    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[BaseModel], Awaitable[Any]] | None = None
    terminal: bool = False  # calling it ends the loop; its validated input is the result

    def api_schema(self, eager: bool) -> dict:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        out = {"name": self.name, "description": self.description, "input_schema": schema}
        if eager:
            out["eager_input_streaming"] = True
        return out


@dataclass
class RunContext:
    run_id: int | None = None
    emit: Emit = _no_emit
    token_budget: int = 120_000
    tokens_used: int = 0
    seen_ids: set[str] = field(default_factory=set)  # article ids the agents actually saw

    @property
    def over_budget(self) -> bool:
        return self.tokens_used >= self.token_budget


@dataclass
class LoopResult:
    stop: str  # end_turn | terminal | refusal | max_tokens | budget | steps
    text: str
    terminal_input: BaseModel | None
    messages: list[dict]
    steps: int


async def _record_tool_call(ctx: RunContext, agent: str, name: str, args: dict, content: str,
                            is_error: bool, latency_ms: int) -> None:
    if ctx.run_id is None:
        return
    async with session_scope() as s:
        s.add(ToolCall(run_id=ctx.run_id, agent=agent, tool=name, arguments=args,
                       result_preview=content[:2000], is_error=is_error, latency_ms=latency_ms))


async def _run_tool(ctx: RunContext, agent: str, block, local: dict[str, LocalTool]) -> dict:
    await ctx.emit("tool_call", {"agent": agent, "tool": block.name, "input": block.input})
    args = block.input if isinstance(block.input, dict) else {}
    if block.name in local:
        lt = local[block.name]
        try:
            parsed = lt.input_model.model_validate(args)
            out = await lt.fn(parsed)
            content = out if isinstance(out, str) else json.dumps(out, default=str)
            is_error, latency = False, 0
        except ValidationError as e:
            content, is_error, latency = f"INVALID_INPUT: {e.errors(include_url=False)}", True, 0
        except Exception as e:
            log.exception("local tool %s failed", block.name)
            content, is_error, latency = f"Tool failed: {e}", True, 0
    else:
        res = await registry.execute(block.name, args)
        content, is_error, latency = res.content, res.is_error, res.latency_ms
    ctx.seen_ids.update(CITE_ID.findall(content))
    await _record_tool_call(ctx, agent, block.name, args, content, is_error, latency)
    result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
    if is_error:
        result["is_error"] = True
    return result


async def agent_loop(
    *,
    agent: str,
    task: str,
    system: str,
    messages: list[dict],
    tool_names: list[str],
    local_tools: list[LocalTool],
    ctx: RunContext,
    max_steps: int,
    max_tokens: int = 8000,
    on_text: Callable[[str], Awaitable[None]] | None = None,
) -> LoopResult:
    router = get_router()
    eager = on_text is not None
    tools = registry.schemas(tool_names, eager) + [t.api_schema(eager) for t in local_tools]
    local = {t.name: t for t in local_tools}
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    text = ""

    for step in range(1, max_steps + 1):
        if ctx.over_budget:
            return LoopResult("budget", text, None, messages, step - 1)
        for attempt in range(2):
            try:
                msg = await router.complete(
                    task, system=system_blocks, tools=tools, messages=messages,
                    max_tokens=max_tokens, cache_control={"type": "ephemeral"}, on_text=on_text,
                )
                break
            except ValueError:  # unparseable eagerly-streamed tool JSON: re-issue the turn
                if attempt == 1:
                    raise
        ctx.tokens_used += msg.usage.input_tokens + msg.usage.output_tokens + (
            msg.usage.cache_read_input_tokens or 0
        )
        # Append the full content unchanged (thinking blocks included): history is append-only.
        messages.append({"role": "assistant", "content": msg.content})
        text = "".join(b.text for b in msg.content if b.type == "text")
        tool_uses = [b for b in msg.content if b.type == "tool_use"]

        if msg.stop_reason == "refusal":
            return LoopResult("refusal", text, None, messages, step)
        if msg.stop_reason == "max_tokens":
            return LoopResult("max_tokens", text, None, messages, step)
        if not tool_uses:
            return LoopResult("end_turn", text, None, messages, step)

        terminal = next((b for b in tool_uses if b.name in local and local[b.name].terminal), None)
        if terminal is not None:
            try:
                parsed = local[terminal.name].input_model.model_validate(terminal.input)
                return LoopResult("terminal", text, parsed, messages, step)
            except ValidationError as e:
                # Fall through: return the validation error so the model can fix its submission.
                log.warning("invalid terminal submission from %s: %s", agent, e)

        # Run all tool calls from this turn concurrently; return every result in ONE message.
        results = await asyncio.gather(*(_run_tool(ctx, agent, b, local) for b in tool_uses))
        messages.append({"role": "user", "content": list(results)})

    return LoopResult("steps", text, None, messages, max_steps)
