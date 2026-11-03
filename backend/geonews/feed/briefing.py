"""Daily briefings.

Scheduled path (cheap): hourly cron submits one Message Batch (50% price) for users whose local
time is 06:00; a 10-minute cron collects finished batches and delivers them at ~07:00.
On-demand path: write_briefing() calls the model directly (used by the API and evals).
"""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from anthropic.types.messages.batch_create_params import Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from geonews.agents.prompts import BRIEFING_PROMPT_VERSION, BRIEFING_SYSTEM
from geonews.cache import get_redis
from geonews.config import get_settings
from geonews.db import session_scope
from geonews.feed.ranking import Candidate, rank_for_user, ranker_version
from geonews.llm.router import cost_usd, get_router, text_of
from geonews.models import Briefing, BriefingItem, Event, LLMCall, User, UserProfile

log = logging.getLogger(__name__)
BATCHES_KEY = "briefing:batches"
ITEMS = 7


def briefing_prompt(profile: UserProfile, cands: list[Candidate]) -> str:
    blocks = []
    for c in cands:
        arts = "\n".join(
            f"  [A{a.id}] {a.title} ({a.source}, {a.published_at:%b %d %H:%M}): {a.summary[:300]}"
            for a in sorted(c.articles, key=lambda a: a.published_at, reverse=True)[:3]
        )
        blocks.append(f"Story {c.story.id} (topic: {c.story.topic}, "
                      f"{c.story.article_count} articles):\n{arts}")
    return (
        f"<reader>\nHome: {profile.home_label}\nInterests: {', '.join(profile.interests or [])}\n"
        f"Follows: {', '.join(profile.follows or [])}\nNotes: {profile.memory_summary}\n</reader>\n\n"
        f"<stories ranked=\"most relevant first\">\n" + "\n\n".join(blocks) + "\n</stories>"
    )


def _request_params(profile: UserProfile, cands: list[Candidate]) -> dict:
    return {
        "model": get_settings().briefing_model,
        "max_tokens": 4000,
        "system": [{"type": "text", "text": BRIEFING_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": briefing_prompt(profile, cands)}],
        "output_config": {"effort": "medium"},
    }


async def _save(user_id: int, body: str, model: str, cands: list[Candidate]) -> int:
    version = ranker_version()
    async with session_scope() as s:
        b = Briefing(user_id=user_id, body_markdown=body, prompt_version=BRIEFING_PROMPT_VERSION,
                     model=model)
        s.add(b)
        await s.flush()
        for pos, c in enumerate(cands):
            s.add(BriefingItem(briefing_id=b.id, story_id=c.story.id, position=pos, score=c.score,
                               features=c.features, ranker_version=version))
            s.add(Event(user_id=user_id, kind="impression", story_id=c.story.id, briefing_id=b.id,
                        meta={"position": pos, "surface": "briefing"}))
        return b.id


async def _load_profile(user_id: int) -> UserProfile | None:
    async with session_scope() as s:
        return await s.get(UserProfile, user_id)


async def write_briefing(user_id: int) -> dict:
    profile = await _load_profile(user_id)
    cands = (await rank_for_user(profile))[:ITEMS] if profile else []
    if not cands:
        return {"briefing_id": None, "body": "Nothing new near you yet. Check back later."}
    params = _request_params(profile, cands)
    model = params.pop("model")
    params.pop("output_config")
    msg = await get_router().complete("briefing", **params)
    body = text_of(msg) if msg.stop_reason != "refusal" else "Briefing unavailable today."
    bid = await _save(user_id, body, model, cands)
    return {"briefing_id": bid, "body": body}


def _due(profile: UserProfile, hour: int) -> bool:
    try:
        tz = ZoneInfo(profile.timezone or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).hour == hour


async def submit_due_batch(hour: int = 6) -> str | None:
    """Rank for every due user now, write all briefings in one batch."""
    async with session_scope() as s:
        profiles = list((await s.execute(select(UserProfile))).scalars())
    requests, pending = [], {}
    for p in profiles:
        if not _due(p, hour):
            continue
        cands = (await rank_for_user(p))[:ITEMS]
        if not cands:
            continue
        cid = f"u{p.user_id}"
        requests.append(Request(custom_id=cid, params=_request_params(p, cands)))
        pending[cid] = {"user_id": p.user_id, "story_ids": [c.story.id for c in cands],
                        "scores": [c.score for c in cands], "features": [c.features for c in cands]}
    if not requests:
        return None
    batch = await get_router().client.messages.batches.create(requests=requests)
    await get_redis().hset(BATCHES_KEY, batch.id, json.dumps(pending))
    log.info("submitted briefing batch %s with %d users", batch.id, len(requests))
    return batch.id


async def collect_batches() -> int:
    """Deliver every finished batch. Safe to run repeatedly."""
    from geonews.feed.delivery import deliver
    from geonews.models import Story

    r = get_redis()
    client = get_router().client
    delivered = 0
    for batch_id, raw in (await r.hgetall(BATCHES_KEY)).items():
        batch = await client.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            continue
        pending = json.loads(raw)
        async for res in await client.messages.batches.results(batch_id):
            info = pending.get(res.custom_id)
            if info is None or res.result.type != "succeeded":
                log.warning("briefing %s: %s", res.custom_id, res.result.type)
                continue
            msg = res.result.message
            async with session_scope() as s:
                stories = {st.id: st for st in (await s.execute(
                    select(Story).where(Story.id.in_(info["story_ids"])))).scalars()}
                s.add(LLMCall(task="briefing", model=msg.model, user_id=info["user_id"],
                              input_tokens=msg.usage.input_tokens,
                              output_tokens=msg.usage.output_tokens,
                              cost_usd=cost_usd(msg.model, msg.usage) * 0.5, latency_ms=0,
                              stop_reason=msg.stop_reason))
            cands = [Candidate(stories[sid], [], f, sc) for sid, sc, f in
                     zip(info["story_ids"], info["scores"], info["features"], strict=True)
                     if sid in stories]
            body = text_of(msg)
            bid = await _save(info["user_id"], body, msg.model, cands)
            await deliver(info["user_id"], bid, body)
            delivered += 1
        await r.hdel(BATCHES_KEY, batch_id)
    return delivered


async def send_due_briefings() -> int:
    await submit_due_batch()
    return await collect_batches()


async def latest_briefing(user_id: int) -> dict | None:
    async with session_scope() as s:
        b = (await s.execute(
            select(Briefing).where(Briefing.user_id == user_id)
            .options(selectinload(Briefing.items)).order_by(Briefing.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if b is None:
            return None
        return {"id": b.id, "created_at": b.created_at.isoformat(), "body": b.body_markdown,
                "story_ids": [i.story_id for i in sorted(b.items, key=lambda i: i.position)]}


async def users_with_profiles() -> list[int]:
    async with session_scope() as s:
        return list((await s.execute(select(User.id).join(UserProfile))).scalars())
