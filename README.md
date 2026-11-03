# GeoNews

A geo-aware news agent. GeoNews ingests real news feeds, clusters articles into stories, and geocodes them. It ranks stories per user, writes each user a daily briefing, and answers questions through a multi-agent pipeline. Every answer cites its sources and is checked by a verifier before the user sees it.

The architecture diagrams are in [`docs/geonews-flowchart.md`](docs/geonews-flowchart.md).

```
backend/
  geonews/
    ingest/        RSS + GDELT -> extract -> MinHash dedupe/cluster -> geo -> classify -> embed (arq workers)
    llm/           router (task->model, fallbacks, cost/usage logging, OTel), classifier, embeddings
    tools/         tool registry: news / geo / memory tools (Pydantic-validated)
    mcp_servers/   the same tools exported as MCP servers (stdio or streamable HTTP)
    agents/        orchestrator, retriever + researcher subagents, verifier, skills/, agent loop
    feed/          candidate generation, ranking (heuristic -> LightGBM), briefings (Batch API), delivery
    api/           FastAPI: user routes (feed, chat SSE, events) + admin routes (traces, metrics, evals, labels)
  geonews_evals/    datasets, LLM judges, CI regression gate, judge calibration, nightly online eval
  ml/              data export, LoRA SFT, DPO, LightGBM ranker, LoRA classifier server
web/               Next.js: feed, chat, settings, admin (metrics, trace viewer, evals, labeling)
```

## Run it locally

Requirements: Docker, [uv](https://docs.astral.sh/uv/), Node 22+, and an Anthropic API key.

```bash
cp .env.example backend/.env           # set ANTHROPIC_API_KEY and GEONEWS_NOMINATIM_USER_AGENT
make infra                             # Postgres+pgvector on :5432, Redis on :6379
cd backend && uv sync --extra embed --extra ml && cd ..
make api                               # http://localhost:8000/docs
make worker                            # ingestion every 15 min + briefing/eval crons
cd web && npm install && cd .. && make web   # http://localhost:3000
```

If port 5432 is already taken by a local Postgres, map the container to another port and update `GEONEWS_DATABASE_URL` to match.

Add your city's news feeds to `backend/feeds.txt`. Local outlets are what make the product useful. Then open the web app, sign up, and set your home area in Settings.

You can also run everything at once with `docker compose up --build`.

## How a question is answered

1. **Context.** The orchestrator's system prompt and tool definitions are cached. The user's profile, memory notes and recent questions come after the cache breakpoint as one small block, so they don't invalidate the cache.
2. **Delegation.** The orchestrator, which defaults to Claude Opus 5.5, calls `find_stories`. That starts a retriever subagent on Haiku 5.5 with the `search_stories`, `stories_near` and `geocode` tools. It then calls `research_articles` one or more times, in parallel. Each call starts a researcher subagent with the `get_article` and `get_story_timeline` tools.
3. **Compressed hand-offs.** Each subagent ends by calling a `submit_findings` tool that takes a strict schema (cited claims, conflicts, gaps). The orchestrator never sees raw article text, so its context stays small no matter how much the subagents read.
4. **Skills.** Only one-line skill descriptions are in the system prompt. The orchestrator calls `load_skill` to pull in a full instruction pack (`local-government`, `breaking-news`, `commute-impact`) when it's relevant.
5. **Verification.** Two checks run after the draft is written:
   - A deterministic check confirms every cited id was actually returned by a tool during the run.
   - A Haiku verifier checks each sentence against the cited article text.

   If anything is unsupported, the orchestrator gets one chance to revise. After that, unsupported sentences are removed and the answer says so.
6. **Guardrails.**
   - Step and token budgets per run
   - A per-user daily cost cap, enforced in Redis
   - Rate limits
   - Refusal handling, with server-side fallbacks on Opus
   - Append-only message history, so thinking blocks stay valid

Everything is recorded:
- **Runs:** `agent_runs`, `tool_calls` and `llm_calls` rows, with cost and latency
- **Behavior:** impressions, clicks, dwell time, ratings and hides in `events`
- **Traces:** OTel spans, exported to Langfuse or any OTLP endpoint

## Evals

```bash
cd backend
uv run geonews-eval seed                     # synthetic fixture corpus (fictional towns)
uv run geonews-eval run --suite classify     # classifier accuracy (no DB needed)
uv run geonews-eval run --suite qa           # end-to-end agent: judges + trajectory metrics
uv run geonews-eval run --suite all --baseline geonews_evals/baseline.json   # CI gate, exit 1 on regression
uv run geonews-eval calibrate                # judge vs human labels (Cohen's kappa)
```

The QA suite measures three kinds of metric:
- **Deterministic:** citation validity, must-cite recall, verifier pass rate, cost, p95 latency
- **Trajectory:** whether the agent delegated, whether it loaded the right skill, and repeated identical tool calls
- **LLM judges, one rubric each:** faithfulness, fact recall, usefulness, correct abstention, and outdated facts that must not appear

CI runs the eval gate on any pull request that touches `agents/`, `tools/`, `llm/` or `geonews_evals/`.

**`baseline.json` contains placeholder thresholds.** Replace them with the numbers from your first real run before relying on the gate.

Labels feed back into the system:
- **Faithfulness labels** (admin → Labeling) calibrate the judge.
- **Relevance labels** override the classifier's labels in the LoRA training data.
- **Pairwise picks** become DPO pairs.
- **Nightly sample:** a job judges a sample of production runs. Confirmed failures should be added to `datasets/qa.jsonl`.

## Fine-tuning vs. prompting vs. scaffolding

| Problem | Approach | Where |
|---|---|---|
| Article topic/locality/newsworthiness (high volume) | Prompted Haiku now; LoRA on a small open model once enough labels exist | `llm/classifier.py`, `ml/lora_train.py`, `ml/serve_classifier.py`, `GEONEWS_CLASSIFIER_BACKEND=lora` |
| Summary style users prefer | DPO on pairwise picks | `ml/dpo_train.py` |
| Feed ranking | Heuristic, then LightGBM LambdaRank on clicks (only shipped if it beats the heuristic on NDCG@5) | `feed/ranking.py`, `ml/ranker_train.py` |
| Grounding / factuality | Scaffolding: verifier + citation checks, not training | `agents/verifier.py` |

## Use the tools from Claude Code

```bash
claude mcp add geonews-news -- uv run --directory backend python -m geonews.mcp_servers.server news
claude mcp add geonews-geo  -- uv run --directory backend python -m geonews.mcp_servers.server geo
```

## Before real users

These are v0 shortcuts:

- **Auth is a bearer token issued at signup.** Replace it with magic-link email before inviting people outside your circle.
- **The schema is created with `create_all`.** Move to Alembic once the tables settle.
- **Geo filtering uses lat/lon bounding boxes plus haversine distance.** Switch to PostGIS when you need polygons, such as city or neighborhood boundaries.
- **Nominatim's public instance allows about 1 request per second.** The geocode cache absorbs most lookups. Self-host Nominatim or use a paid geocoder at scale.
- **Feed licensing:** respect each feed's terms. Store and show only summaries plus links. Keep full text for internal retrieval only.
