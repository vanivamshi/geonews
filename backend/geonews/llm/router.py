"""Single entry point for model calls.

Routes each *task* to a model, applies per-model request rules (fallbacks, effort), records
usage/cost for every call, and emits an OTel span. Swapping a task from prompted Claude to the
self-hosted LoRA model is a one-line change in TASK_ROUTES (see llm/classifier.py).
"""

import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import anthropic
from anthropic.types.beta import BetaMessage

from geonews.config import get_settings
from geonews.telemetry import tracer

log = logging.getLogger(__name__)

# Attribution for usage records without threading ids through every call site.
current_run_id: ContextVar[int | None] = ContextVar("current_run_id", default=None)
current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)


@dataclass(frozen=True)
class Price:
    input: float  # $ per 1M tokens
    output: float
    cache_read: float


PRICES = {
    "claude-opus-5-5": Price(4.00, 20.00, 0.20),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.20),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01),
}
# Models with server-side refusal fallback. Haiku 5.5 has none (a fallbacks param would 400).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


def task_routes() -> dict[str, tuple[str, str]]:
    """task -> (model, effort)."""
    s = get_settings()
    return {
        "orchestrate": (s.orchestrator_model, "medium"),
        "retrieve": (s.subagent_model, "low"),
        "research": (s.subagent_model, "low"),
        "verify": (s.subagent_model, "medium"),
        "classify": (s.subagent_model, "low"),
        "briefing": (s.briefing_model, "medium"),
        "memory": (s.subagent_model, "low"),
        "judge": (s.judge_model, "medium"),
    }


def cost_usd(model: str, usage: Any) -> float:
    p = PRICES.get(model)
    if p is None:
        return 0.0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        usage.input_tokens * p.input
        + cache_write * p.input * 1.25
        + cache_read * p.cache_read
        + usage.output_tokens * p.output
    ) / 1_000_000


UsageSink = Callable[[dict], Awaitable[None]]


async def _db_sink(record: dict) -> None:
    from geonews.cache import add_user_spend
    from geonews.db import session_scope
    from geonews.models import LLMCall

    try:
        async with session_scope() as s:
            s.add(LLMCall(**record))
        if record.get("user_id"):
            await add_user_spend(record["user_id"], record["cost_usd"])
    except Exception:  # usage logging must never break a user request
        log.exception("failed to record llm usage")


class LLMRouter:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None, sink: UsageSink | None = None):
        self.client = client or anthropic.AsyncAnthropic()
        self.sink = sink or _db_sink

    def _params(self, task: str, params: dict) -> dict:
        model, effort = task_routes()[task]
        params = {"model": model, **params}
        output_config = {"effort": effort, **params.pop("output_config", {})}
        params["output_config"] = output_config
        if params["model"] in FALLBACK_MODELS:
            params["betas"] = [*params.get("betas", []), "server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"
        return params

    async def complete(
        self,
        task: str,
        *,
        on_text: Callable[[str], Awaitable[None]] | None = None,
        **params: Any,
    ) -> BetaMessage:
        """One model turn. Streams (and forwards text deltas to on_text) when on_text is given."""
        params = self._params(task, params)
        start = time.perf_counter()
        with tracer.start_as_current_span(f"llm.{task}") as span:
            span.set_attribute("gen_ai.request.model", params["model"])
            if on_text is None:
                msg = await self.client.beta.messages.create(**params)
            else:
                async with self.client.beta.messages.stream(**params) as stream:
                    async for event in stream:
                        if event.type == "text":
                            await on_text(event.text)
                    msg = await stream.get_final_message()
            latency_ms = int((time.perf_counter() - start) * 1000)
            cost = cost_usd(msg.model, msg.usage)
            span.set_attribute("gen_ai.response.model", msg.model)
            span.set_attribute("gen_ai.usage.input_tokens", msg.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", msg.usage.output_tokens)
            span.set_attribute("geonews.cost_usd", cost)
            span.set_attribute("geonews.stop_reason", msg.stop_reason or "")
        await self.sink(
            {
                "task": task,
                "model": msg.model,
                "run_id": current_run_id.get(),
                "user_id": current_user_id.get(),
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
                "cache_read_tokens": msg.usage.cache_read_input_tokens or 0,
                "cache_write_tokens": msg.usage.cache_creation_input_tokens or 0,
                "cost_usd": cost,
                "latency_ms": latency_ms,
                "stop_reason": msg.stop_reason,
            }
        )
        return msg

    async def complete_json(self, task: str, schema: dict, **params: Any) -> dict:
        """Structured output: the response text is guaranteed to match `schema`."""
        output_config = {"format": {"type": "json_schema", "schema": schema}}
        msg = await self.complete(task, output_config=output_config, **params)
        if msg.stop_reason == "refusal":
            raise RefusalError(task)
        text = next((b.text for b in msg.content if b.type == "text"), "")
        return json.loads(text)


class RefusalError(RuntimeError):
    pass


def text_of(msg: BetaMessage) -> str:
    return "".join(b.text for b in msg.content if b.type == "text")


_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router
