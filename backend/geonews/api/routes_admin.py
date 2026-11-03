"""Internal tools API: traces, metrics, evals, labeling, preference pairs."""

import random
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from geonews.api.deps import require_admin
from geonews.cache import get_redis
from geonews.db import get_session
from geonews.ingest.worker import DEAD_LETTER
from geonews.llm.router import get_router, text_of
from geonews.models import (
    AgentRun,
    Article,
    EvalRun,
    EvalScore,
    Label,
    LLMCall,
    Preference,
)

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("/runs")
async def runs(limit: int = 50, status: str | None = None,
               session: AsyncSession = Depends(get_session)):
    stmt = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(min(limit, 200))
    if status:
        stmt = stmt.where(AgentRun.status == status)
    return [{"id": r.id, "kind": r.kind, "question": r.question, "status": r.status,
             "steps": r.steps, "cost_usd": r.cost_usd, "latency_ms": r.latency_ms,
             "all_supported": (r.verdict or {}).get("all_supported"),
             "created_at": r.created_at.isoformat()}
            for r in (await session.execute(stmt)).scalars()]


@router.get("/runs/{run_id}")
async def run_detail(run_id: int, session: AsyncSession = Depends(get_session)):
    r = (await session.execute(
        select(AgentRun).where(AgentRun.id == run_id).options(selectinload(AgentRun.tool_calls))
    )).scalar_one_or_none()
    if r is None:
        raise HTTPException(404)
    llm = (await session.execute(select(LLMCall).where(LLMCall.run_id == run_id)
                                 .order_by(LLMCall.id))).scalars()
    return {
        "id": r.id, "question": r.question, "answer": r.answer, "status": r.status,
        "verdict": r.verdict, "citations": r.citations, "prompt_version": r.prompt_version,
        "trace_id": r.trace_id, "cost_usd": r.cost_usd, "latency_ms": r.latency_ms,
        "tokens": {"input": r.input_tokens, "output": r.output_tokens, "cache_read": r.cache_read_tokens},
        "tool_calls": [{"agent": t.agent, "tool": t.tool, "arguments": t.arguments,
                        "result_preview": t.result_preview, "is_error": t.is_error,
                        "latency_ms": t.latency_ms, "at": t.created_at.isoformat()}
                       for t in r.tool_calls],
        "llm_calls": [{"task": c.task, "model": c.model, "input_tokens": c.input_tokens,
                       "output_tokens": c.output_tokens, "cache_read_tokens": c.cache_read_tokens,
                       "cost_usd": c.cost_usd, "latency_ms": c.latency_ms,
                       "stop_reason": c.stop_reason} for c in llm],
    }


@router.get("/metrics")
async def metrics(days: int = 7, session: AsyncSession = Depends(get_session)):
    since = datetime.now(UTC) - timedelta(days=days)
    day = func.date_trunc("day", LLMCall.created_at)
    cost_rows = (await session.execute(
        select(day, LLMCall.task, func.sum(LLMCall.cost_usd), func.count())
        .where(LLMCall.created_at >= since).group_by(day, LLMCall.task).order_by(day)
    )).all()
    run_stats = (await session.execute(
        select(func.count(), func.percentile_cont(0.5).within_group(AgentRun.latency_ms),
               func.percentile_cont(0.95).within_group(AgentRun.latency_ms),
               func.avg(AgentRun.cost_usd), func.count(func.distinct(AgentRun.user_id)))
        .where(AgentRun.created_at >= since, AgentRun.kind == "chat", AgentRun.status != "running")
    )).one()
    status_rows = (await session.execute(
        select(AgentRun.status, func.count()).where(AgentRun.created_at >= since)
        .group_by(AgentRun.status)
    )).all()
    newest = (await session.execute(select(func.max(Article.ingested_at)))).scalar()
    ingested_24h = (await session.execute(
        select(func.count()).where(Article.ingested_at >= datetime.now(UTC) - timedelta(hours=24))
    )).scalar()
    r = get_redis()
    return {
        "cost_by_day_task": [{"day": d.date().isoformat(), "task": t, "cost_usd": float(c), "calls": n}
                             for d, t, c, n in cost_rows],
        "chat": {"runs": run_stats[0], "p50_ms": run_stats[1], "p95_ms": run_stats[2],
                 "avg_cost_usd": float(run_stats[3] or 0), "active_users": run_stats[4]},
        "run_status": dict(status_rows),
        "ingestion": {
            "articles_24h": ingested_24h,
            "lag_minutes": round((datetime.now(UTC) - newest).total_seconds() / 60, 1) if newest else None,
            "queue_depth": await r.zcard("arq:queue"),
            "dead_letters": await r.llen(DEAD_LETTER),
        },
    }


@router.get("/evals")
async def evals(limit: int = 30, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(EvalRun).order_by(EvalRun.created_at.desc())
                                  .limit(limit))).scalars()
    return [{"id": e.id, "suite": e.suite, "git_sha": e.git_sha, "prompt_version": e.prompt_version,
             "model": e.model, "summary": e.summary, "created_at": e.created_at.isoformat()}
            for e in rows]


@router.get("/evals/{eval_id}")
async def eval_detail(eval_id: int, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(EvalScore).where(EvalScore.eval_run_id == eval_id))).scalars()
    return [{"case_id": s.case_id, "metric": s.metric, "score": s.score, "detail": s.detail}
            for s in rows]


@router.get("/label/queue")
async def label_queue(kind: str = "faithfulness", session: AsyncSession = Depends(get_session)):
    """Next items needing a human label. Runs the judge scored low are prioritized."""
    if kind == "relevance":
        labeled = select(Label.target_id).where(Label.kind == "relevance")
        arts = (await session.execute(
            select(Article).where(Article.id.not_in(labeled)).order_by(func.random()).limit(10)
        )).scalars()
        return [{"target_type": "article", "target_id": a.id, "title": a.title,
                 "summary": a.summary[:500], "topic": a.topic, "locality": a.locality,
                 "newsworthiness": a.newsworthiness} for a in arts]
    labeled = select(Label.target_id).where(Label.kind == kind, Label.target_type == "agent_run")
    runs_ = (await session.execute(
        select(AgentRun).where(AgentRun.status.in_(["done", "degraded"]), AgentRun.id.not_in(labeled))
        .order_by(AgentRun.created_at.desc()).limit(10)
    )).scalars()
    return [{"target_type": "agent_run", "target_id": r.id, "question": r.question,
             "answer": r.answer, "verdict": r.verdict} for r in runs_]


class LabelIn(BaseModel):
    kind: str
    target_type: str
    target_id: int
    value: float = Field(ge=-1, le=5)
    note: str = ""


@router.post("/labels")
async def add_label(body: LabelIn, session: AsyncSession = Depends(get_session)):
    session.add(Label(**body.model_dump()))
    return {"ok": True}


PAIR_STYLES = [
    "Summarize this story for a busy local resident in 2-3 sentences. Lead with what changes for them.",
    "Summarize this story in 2-3 neutral, factual sentences, in the order events happened.",
]


@router.get("/pairs/next")
async def next_pair(session: AsyncSession = Depends(get_session)):
    """Generate two candidate summaries of a recent article for pairwise preference labeling."""
    art = (await session.execute(
        select(Article).where(Article.body != "").order_by(Article.ingested_at.desc()).limit(50)
    )).scalars().all()
    if not art:
        raise HTTPException(404, "no articles yet")
    a = random.choice(art)
    prompt = f"{a.title}\n\n{a.body[:4000]}"
    outs = []
    for style in PAIR_STYLES:
        msg = await get_router().complete("research", max_tokens=400, system=style,
                                          messages=[{"role": "user", "content": prompt}])
        outs.append(text_of(msg))
    order = random.sample([0, 1], 2)  # randomize position to avoid position bias in labels
    return {"article_id": a.id, "prompt": prompt, "a": outs[order[0]], "b": outs[order[1]]}


class PreferenceIn(BaseModel):
    prompt: str
    chosen: str
    rejected: str


@router.post("/preferences")
async def add_preference(body: PreferenceIn, session: AsyncSession = Depends(get_session)):
    session.add(Preference(source="labeling_ui", **body.model_dump()))
    return {"ok": True}
