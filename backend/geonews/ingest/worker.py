"""arq worker: queue-driven ingestion plus scheduled jobs.

Run with:  uv run arq geonews.ingest.worker.WorkerSettings

Jobs:
  poll_sources   (cron, every 15 min) fetch feeds -> enqueue one ingest_item job per new URL
  ingest_item    process one article; arq retries with backoff, then dead-letters
  submit_digests (cron, hourly) batch-write briefings for users whose local time is 06:00
  collect_digests(cron, 10 min) deliver finished briefing batches
  nightly_eval   (cron, 03:00 UTC) sample production runs and judge them
"""

import json
import logging
from dataclasses import asdict

import httpx
from arq import Retry, cron
from arq.connections import RedisSettings

from geonews.cache import get_redis
from geonews.config import get_settings
from geonews.ingest.pipeline import already_ingested, process_item
from geonews.ingest.sources import RawItem, fetch_all
from geonews.telemetry import setup_telemetry

log = logging.getLogger(__name__)
MAX_TRIES = 4
DEAD_LETTER = "ingest:dead_letter"


async def startup(ctx: dict) -> None:
    setup_telemetry("geonews-worker")
    from geonews.db import init_db

    await init_db()
    ctx["http"] = httpx.AsyncClient()


async def shutdown(ctx: dict) -> None:
    await ctx["http"].aclose()


async def poll_sources(ctx: dict) -> int:
    items = await fetch_all()
    queued = 0
    for item in items:
        if await already_ingested(item.url_hash):
            continue
        # _job_id makes enqueueing idempotent across overlapping polls.
        job = await ctx["redis"].enqueue_job(
            "ingest_item", asdict(item), _job_id=f"ingest:{item.url_hash}"
        )
        queued += job is not None
    log.info("poll_sources: %d fetched, %d queued", len(items), queued)
    return queued


async def ingest_item(ctx: dict, raw: dict) -> int | None:
    item = RawItem(**raw)
    try:
        return await process_item(item, ctx["http"])
    except Exception as e:
        if ctx["job_try"] >= MAX_TRIES:
            log.exception("dead-lettering %s", item.url)
            await get_redis().lpush(DEAD_LETTER, json.dumps({"item": raw, "error": repr(e)}, default=str))
            return None
        raise Retry(defer=ctx["job_try"] * 30) from e


async def submit_digests(ctx: dict) -> str | None:
    from geonews.feed.briefing import submit_due_batch

    return await submit_due_batch(hour=6)


async def collect_digests(ctx: dict) -> int:
    from geonews.feed.briefing import collect_batches

    return await collect_batches()


async def nightly_eval(ctx: dict) -> dict:
    from geonews_evals.online import judge_recent_runs

    return await judge_recent_runs(limit=50)


class WorkerSettings:
    functions = [ingest_item, poll_sources]
    cron_jobs = [
        cron(poll_sources, minute={0, 15, 30, 45}, run_at_startup=True),
        cron(submit_digests, minute=0),
        cron(collect_digests, minute={5, 15, 25, 35, 45, 55}),
        cron(nightly_eval, hour=3, minute=0),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 8
    job_timeout = 120
    max_tries = MAX_TRIES + 1
