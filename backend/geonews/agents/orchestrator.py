"""Orchestrator: plans, delegates to subagents, drafts a cited answer, and gets it verified.

Flow (see geonews-flowchart.md, diagram 3):
  context -> plan/delegate loop (streams draft) -> verifier -> [revise | degrade] -> final
"""

import time
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from geonews.agents import subagents
from geonews.agents.loop import Emit, LocalTool, RunContext, _no_emit, agent_loop
from geonews.agents.prompts import ORCHESTRATOR_SYSTEM, PROMPT_VERSION
from geonews.agents.skills import get_skill, skills_index
from geonews.agents.verifier import cited_ids, degrade, verify
from geonews.cache import get_user_spend
from geonews.config import get_settings
from geonews.db import session_scope
from geonews.llm.router import current_run_id, current_user_id
from geonews.models import AgentRun, LLMCall, UserProfile
from geonews.telemetry import current_trace_id, tracer


class FindStoriesInput(BaseModel):
    task: str = Field(description="What coverage to find, including place and timeframe.")


class ResearchInput(BaseModel):
    question: str = Field(description="The specific question these articles should answer.")
    article_ids: list[str] = Field(min_length=1, max_length=6)


class LoadSkillInput(BaseModel):
    name: str


async def build_context(user_id: int | None, profile_override: dict | None) -> str:
    profile = profile_override
    recent: list[str] = []
    if profile is None and user_id is not None:
        async with session_scope() as s:
            p = await s.get(UserProfile, user_id)
            if p is not None:
                profile = {"home": p.home_label, "radius_km": p.radius_km,
                           "interests": p.interests, "follows": p.follows,
                           "memory": p.memory_summary}
            rows = await s.execute(
                select(AgentRun.question).where(AgentRun.user_id == user_id, AgentRun.kind == "chat",
                                                AgentRun.status == "done")
                .order_by(AgentRun.created_at.desc()).limit(3)
            )
            recent = list(rows.scalars())
    profile = profile or {}
    lines = [
        f"Current time: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        f"Home area: {profile.get('home') or 'unknown'} (radius {profile.get('radius_km', 50)} km)",
        f"Interests: {', '.join(profile.get('interests') or []) or 'none set'}",
        f"Follows: {', '.join(profile.get('follows') or []) or 'none'}",
    ]
    if profile.get("memory"):
        lines.append(f"Memory notes:\n{profile['memory']}")
    if recent:
        lines.append("Recent questions: " + " | ".join(recent))
    return "<user_context>\n" + "\n".join(lines) + "\n</user_context>"


async def _run_totals(run_id: int) -> dict:
    async with session_scope() as s:
        row = (await s.execute(
            select(func.coalesce(func.sum(LLMCall.input_tokens), 0),
                   func.coalesce(func.sum(LLMCall.output_tokens), 0),
                   func.coalesce(func.sum(LLMCall.cache_read_tokens), 0),
                   func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
            .where(LLMCall.run_id == run_id)
        )).one()
    return {"input_tokens": row[0], "output_tokens": row[1], "cache_read_tokens": row[2],
            "cost_usd": float(row[3])}


async def answer_question(
    question: str,
    user_id: int | None = None,
    emit: Emit = _no_emit,
    kind: str = "chat",
    profile_override: dict | None = None,
) -> dict:
    settings = get_settings()
    start = time.perf_counter()
    current_user_id.set(user_id)

    if user_id is not None and await get_user_spend(user_id) >= settings.user_daily_cost_cap_usd:
        msg = "You've reached today's usage limit. Your briefing will still arrive tomorrow."
        await emit("final", {"answer": msg, "citations": [], "verdict": {}})
        return {"answer": msg, "citations": [], "verdict": {}, "status": "capped"}

    async with session_scope() as s:
        run = AgentRun(user_id=user_id, kind=kind, question=question, prompt_version=PROMPT_VERSION,
                       trace_id=current_trace_id())
        s.add(run)
        await s.flush()
        run_id = run.id
    current_run_id.set(run_id)
    await emit("run", {"run_id": run_id})
    try:
        return await _orchestrate(question, user_id, emit, profile_override, run_id, start)
    except Exception as e:
        async with session_scope() as s:
            run = await s.get(AgentRun, run_id)
            run.status, run.verdict = "error", {"error": type(e).__name__}
            run.latency_ms = int((time.perf_counter() - start) * 1000)
        raise


async def _orchestrate(question: str, user_id: int | None, emit: Emit, profile_override: dict | None,
                       run_id: int, start: float) -> dict:
    settings = get_settings()
    ctx = RunContext(run_id=run_id, emit=emit, token_budget=settings.agent_token_budget)

    async def find_stories(args: FindStoriesInput):
        return await subagents.run_retriever(args.task, ctx)

    async def research_articles(args: ResearchInput):
        return await subagents.run_researcher(args.question, args.article_ids, ctx)

    async def load_skill(args: LoadSkillInput):
        body = get_skill(args.name)
        await ctx.emit("skill", {"name": args.name})
        return body or f"No skill named {args.name}"

    local_tools = [
        LocalTool("find_stories", "Delegate to the retriever subagent: finds relevant articles "
                  "and returns their ids with one-line relevance notes.", FindStoriesInput,
                  find_stories),
        LocalTool("research_articles", "Delegate to a researcher subagent: reads the given "
                  "articles and returns cited claims, conflicts, and gaps. Call in parallel for "
                  "different article sets.", ResearchInput, research_articles),
        LocalTool("load_skill", "Load a skill's instructions by name.", LoadSkillInput, load_skill),
    ]
    tool_names = ["remember_preference"] if user_id is not None else []

    async def on_text(delta: str) -> None:
        await emit("answer_delta", {"text": delta})

    with tracer.start_as_current_span("agent.orchestrate") as span:
        span.set_attribute("geonews.run_id", run_id)
        messages = [{"role": "user", "content": [
            {"type": "text", "text": await build_context(user_id, profile_override)},
            {"type": "text", "text": question},
        ]}]
        system = ORCHESTRATOR_SYSTEM.format(skills=skills_index())
        result = await agent_loop(
            agent="orchestrator", task="orchestrate", system=system, messages=messages,
            tool_names=tool_names, local_tools=local_tools, ctx=ctx,
            max_steps=settings.agent_max_steps, max_tokens=16000, on_text=on_text,
        )
        steps = result.steps
        answer = result.text.strip()
        verdict: dict = {}
        status = "done"

        if result.stop == "refusal":
            answer, status = "I can't help with that request.", "refused"
        elif not answer:
            answer, status = "I ran out of time researching this. Try a narrower question.", result.stop
        else:
            for round_ in range(settings.agent_max_verify_rounds + 1):
                await emit("verifying", {"round": round_})
                verdict = await verify(answer, ctx.seen_ids)
                if verdict["all_supported"]:
                    break
                if round_ == settings.agent_max_verify_rounds or ctx.over_budget:
                    answer = degrade(answer, verdict)
                    status = "degraded"
                    break
                feedback = "\n".join(f"- \"{s['text']}\": {s['reason']}" for s in verdict["unsupported"])
                messages.append({"role": "user", "content":
                    "A verifier found these sentences unsupported by their cited sources:\n"
                    f"{feedback}\n\nRevise the full answer: fix or remove them. You may research "
                    "more if needed. Output only the revised answer."})
                await emit("revising", {"unsupported": len(verdict["unsupported"])})
                revised = await agent_loop(
                    agent="orchestrator", task="orchestrate", system=system, messages=messages,
                    tool_names=tool_names, local_tools=local_tools, ctx=ctx, max_steps=3,
                    max_tokens=16000, on_text=on_text,  # same tool defs -> cache stays warm
                )
                steps += revised.steps
                if revised.text.strip():
                    answer = revised.text.strip()

        citations = [i for i in cited_ids(answer) if i in ctx.seen_ids]
        totals = await _run_totals(run_id)
        latency_ms = int((time.perf_counter() - start) * 1000)
        span.set_attribute("geonews.status", status)
        async with session_scope() as s:
            run = await s.get(AgentRun, run_id)
            run.answer, run.citations, run.verdict = answer, [{"id": c} for c in citations], verdict
            run.status, run.steps, run.latency_ms = status, steps, latency_ms
            run.input_tokens, run.output_tokens = totals["input_tokens"], totals["output_tokens"]
            run.cache_read_tokens, run.cost_usd = totals["cache_read_tokens"], totals["cost_usd"]

    final = {"run_id": run_id, "answer": answer, "citations": citations, "verdict": verdict,
             "status": status, "cost_usd": totals["cost_usd"], "latency_ms": latency_ms}
    await emit("final", final)
    return final
