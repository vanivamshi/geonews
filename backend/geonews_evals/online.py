"""Nightly online eval: judge a sample of yesterday's production chat runs.

Low scorers surface first in the labeling queue; once a human confirms a failure, add it to
datasets/qa.jsonl so the regression suite grows from real traffic (the data flywheel).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from geonews.agents.verifier import cited_ids, load_sources
from geonews.db import session_scope
from geonews.models import AgentRun, EvalRun, EvalScore
from geonews_evals.judges import JUDGE_VERSION, judge_faithfulness


async def judge_recent_runs(limit: int = 50) -> dict:
    since = datetime.now(UTC) - timedelta(days=1)
    async with session_scope() as s:
        runs = list((await s.execute(
            select(AgentRun).where(AgentRun.kind == "chat", AgentRun.created_at >= since,
                                   AgentRun.status.in_(["done", "degraded"]))
            .order_by(func.random()).limit(limit)
        )).scalars())
    scored = []
    for r in runs:
        v = await judge_faithfulness(r.answer, await load_sources(cited_ids(r.answer)))
        scored.append((r.id, v))
    mean = sum(v["score"] for _, v in scored) / len(scored) if scored else None
    async with session_scope() as s:
        er = EvalRun(suite="online-faithfulness", git_sha=None, prompt_version=JUDGE_VERSION,
                     model="production", summary={"n": len(scored), "faithfulness": mean})
        s.add(er)
        await s.flush()
        for run_id, v in scored:
            s.add(EvalScore(eval_run_id=er.id, case_id=f"run-{run_id}", metric="faithfulness",
                            score=v["score"], detail=v))
    return {"n": len(scored), "faithfulness": mean}
