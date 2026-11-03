"""Offline eval runner + CI gate.

  geonews-eval seed                          # load fixture corpus
  geonews-eval run --suite classify          # no DB needed
  geonews-eval run --suite qa --limit 3      # needs DB + fixtures
  geonews-eval run --suite all --baseline geonews_evals/baseline.json   # exit 1 on regression
  geonews-eval calibrate                     # judge vs human agreement

Every run writes a JSON report to geonews_evals/reports/ and, if a DB is reachable, EvalRun /
EvalScore rows that the admin eval dashboard reads.
"""

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from geonews.config import get_settings

HERE = Path(__file__).parent
DATASETS = HERE / "datasets"
REPORTS = HERE / "reports"


def load_jsonl(name: str, limit: int | None = None) -> list[dict]:
    rows = [json.loads(ln) for ln in (DATASETS / name).read_text().splitlines() if ln.strip()]
    return rows[:limit] if limit else rows


def git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return None


# ---------------------------------------------------------------- classify suite

async def run_classify(limit: int | None) -> tuple[list[dict], dict]:
    from geonews.llm.classifier import classify

    scores = []
    for case in load_jsonl("classify.jsonl", limit):
        out = await classify(case["title"], case["body"])
        topic_ok = out["topic"] == case["topic"] or out["topic"] in case.get("topic_alternates", [])
        loc_ok = out["locality"] == case["locality"] or out["locality"] in case.get("locality_alternates", [])
        nw = out["newsworthiness"]
        nw_ok = nw >= case.get("newsworthiness_min", 0) and nw <= case.get("newsworthiness_max", 1)
        for metric, val in (("topic_acc", topic_ok), ("locality_acc", loc_ok), ("newsworthiness_ok", nw_ok)):
            scores.append({"case_id": case["id"], "metric": metric, "score": float(val), "detail": out})
    return scores, {"backend": get_settings().classifier_backend}


# ---------------------------------------------------------------- qa suite

async def run_qa(limit: int | None) -> tuple[list[dict], dict]:
    from geonews.agents.orchestrator import answer_question
    from geonews.agents.verifier import load_sources
    from geonews_evals import judges
    from geonews_evals.seed import seed

    keymap = await seed()
    scores = []
    for case in load_jsonl("qa.jsonl", limit):
        events: list[tuple[str, dict]] = []

        async def emit(kind: str, data: dict, _events=events) -> None:
            if kind != "answer_delta":
                _events.append((kind, data))

        res = await answer_question(case["question"], user_id=None, emit=emit, kind="eval",
                                    profile_override=case["profile"])
        answer, cid = res["answer"], case["id"]

        def add(metric: str, value: float, detail: dict | None = None, _cid=cid) -> None:
            scores.append({"case_id": _cid, "metric": metric, "score": float(value), "detail": detail or {}})

        add("latency_s", res["latency_ms"] / 1000)
        add("cost_usd", res["cost_usd"])
        add("citation_validity", res["verdict"].get("citation_validity", 1.0) if res["verdict"] else 1.0)
        add("verifier_pass", float(res["status"] == "done"), {"status": res["status"]})

        # Trajectory: did it delegate, which skill, any repeated identical tool calls (loops)?
        calls = [(d["tool"], json.dumps(d["input"], sort_keys=True)) for k, d in events if k == "tool_call"]
        add("delegated", float(any(t == "find_stories" for t, _ in calls)))
        add("repeated_calls", float(len(calls) - len(set(calls))))
        if case.get("skill"):
            loaded = [d["name"] for k, d in events if k == "skill"]
            add("skill_used", float(case["skill"] in loaded), {"loaded": loaded})

        if case.get("expect_abstain"):
            j = await judges.judge_abstain(case["question"], answer)
            add("abstain_correct", j["score"], j)
            continue

        must = [keymap[k] for k in case["must_cite"] if k in keymap]
        add("must_cite_recall", sum(m in res["citations"] for m in must) / len(must) if must else 1.0,
            {"must": must, "got": res["citations"]})
        sources = await load_sources(res["citations"])
        faith = await judges.judge_faithfulness(answer, sources)
        add("faithfulness", faith["score"], faith)
        cov = await judges.judge_coverage(answer, case["expected_facts"])
        add("fact_recall", sum(cov["covered"]) / len(case["expected_facts"]) if case["expected_facts"] else 1.0, cov)
        use = await judges.judge_usefulness(case["question"], answer, case["profile"])
        add("usefulness", use["score"], use)
        if case.get("forbidden_facts"):
            bad = await judges.judge_coverage(answer, case["forbidden_facts"])
            add("forbidden_absent", float(not any(bad["covered"])), bad)
    from geonews.agents.prompts import PROMPT_VERSION

    return scores, {"prompt_version": PROMPT_VERSION, "judge": judges.JUDGE_VERSION}


# ---------------------------------------------------------------- reporting + gate

def summarize(scores: list[dict]) -> dict[str, float]:
    by_metric = defaultdict(list)
    for s in scores:
        by_metric[s["metric"]].append(s["score"])
    out = {m: round(statistics.mean(v), 4) for m, v in by_metric.items()}
    if "latency_s" in by_metric:
        lat = sorted(by_metric["latency_s"])
        out["latency_p95_s"] = round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2)
    return out


# Metrics where lower is better; everything else is higher-is-better.
LOWER_IS_BETTER = {"latency_s", "latency_p95_s", "cost_usd", "repeated_calls"}


def regressions(summary: dict, baseline: dict, tolerance: float) -> list[str]:
    bad = []
    for metric, base in baseline.items():
        if metric not in summary:
            continue
        cur = summary[metric]
        if metric in LOWER_IS_BETTER:
            if cur > base * (1 + tolerance) + 1e-9:
                bad.append(f"{metric}: {cur} > baseline {base}")
        elif cur < base * (1 - tolerance) - 1e-9:
            bad.append(f"{metric}: {cur} < baseline {base}")
    return bad


async def persist(suite: str, scores: list[dict], summary: dict, meta: dict) -> int | None:
    try:
        from geonews.db import init_db, session_scope
        from geonews.models import EvalRun, EvalScore

        await init_db()
        async with session_scope() as s:
            run = EvalRun(suite=suite, git_sha=git_sha(), prompt_version=meta.get("prompt_version", "-"),
                          model=get_settings().orchestrator_model, summary={**summary, **meta})
            s.add(run)
            await s.flush()
            for sc in scores:
                s.add(EvalScore(eval_run_id=run.id, **sc))
            return run.id
    except Exception as e:
        print(f"(not persisted to DB: {type(e).__name__})", file=sys.stderr)
        return None


async def run_suite(suite: str, limit: int | None) -> dict:
    fn = {"classify": run_classify, "qa": run_qa}[suite]
    scores, meta = await fn(limit)
    summary = summarize(scores)
    run_id = await persist(suite, scores, summary, meta)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{suite}-{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    path.write_text(json.dumps({"suite": suite, "summary": summary, "meta": meta, "scores": scores},
                               indent=2, default=str))
    print(f"\n== {suite} ==  (report: {path}, eval_run_id={run_id})")
    for k, v in sorted(summary.items()):
        print(f"  {k:22s} {v}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(prog="geonews-eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--suite", choices=["classify", "qa", "all"], default="all")
    r.add_argument("--limit", type=int)
    r.add_argument("--baseline", type=Path, help="JSON {suite: {metric: value}}; exit 1 on regression")
    r.add_argument("--tolerance", type=float, default=0.05)
    sub.add_parser("seed")
    sub.add_parser("calibrate")
    args = ap.parse_args()

    from geonews.telemetry import setup_telemetry

    setup_telemetry("geonews-evals")
    if args.cmd == "seed":
        from geonews_evals.seed import seed

        print(json.dumps(asyncio.run(seed()), indent=2))
        return
    if args.cmd == "calibrate":
        from geonews_evals.calibrate import calibrate

        print(json.dumps(asyncio.run(calibrate()), indent=2))
        return

    suites = ["classify", "qa"] if args.suite == "all" else [args.suite]

    async def go() -> dict:
        return {s: await run_suite(s, args.limit) for s in suites}

    results = asyncio.run(go())
    if args.baseline:
        baseline = json.loads(args.baseline.read_text())
        failed = [f"[{s}] {msg}" for s in suites
                  for msg in regressions(results[s], baseline.get(s, {}), args.tolerance)]
        if failed:
            print("\nREGRESSIONS:\n  " + "\n  ".join(failed))
            sys.exit(1)
        print("\nNo regressions vs baseline.")


if __name__ == "__main__":
    main()
