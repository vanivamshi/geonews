"""Judge calibration: agreement between the LLM faithfulness judge and human labels.

Human labels come from the admin labeling UI (Label kind=faithfulness, 1-5 on agent runs).
Both are binarized at >=4 ("acceptable") and compared with Cohen's kappa. Report kappa alongside
any judge-based metric; below ~0.6, fix the rubric before trusting the judge.
"""

from sqlalchemy import select

from geonews.agents.verifier import cited_ids, load_sources
from geonews.db import session_scope
from geonews.models import AgentRun, Label
from geonews_evals.judges import judge_faithfulness


def cohens_kappa(a: list[int], b: list[int]) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    labels = sorted(set(a) | set(b))
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pe = sum((a.count(k) / n) * (b.count(k) / n) for k in labels)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


async def calibrate(limit: int = 200) -> dict:
    async with session_scope() as s:
        rows = (await s.execute(
            select(Label, AgentRun).join(AgentRun, AgentRun.id == Label.target_id)
            .where(Label.kind == "faithfulness", Label.target_type == "agent_run").limit(limit)
        )).all()
    human, judge, disagreements = [], [], []
    for label, run in rows:
        verdict = await judge_faithfulness(run.answer, await load_sources(cited_ids(run.answer)))
        h, j = int(label.value >= 4), int(verdict["score"] >= 4)
        human.append(h)
        judge.append(j)
        if h != j:
            disagreements.append({"run_id": run.id, "human": label.value, "judge": verdict["score"],
                                  "judge_reason": verdict["reason"], "human_note": label.note})
    agree = sum(h == j for h, j in zip(human, judge, strict=True))
    return {
        "n": len(human),
        "agreement": agree / len(human) if human else None,
        "cohens_kappa": cohens_kappa(human, judge) if human else None,
        "disagreements": disagreements[:20],
    }
