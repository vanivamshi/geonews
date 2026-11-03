"""Candidate generation + ranking for feeds and briefings.

Ranking is deliberately not an LLM call: it runs for every user every day, needs to be cheap,
fast and explainable, and it learns from behavior logs. v1 is a hand-weighted linear score; v2 is
a LightGBM model trained on logged impressions/clicks (ml/ranker_train.py) and is used
automatically when its model file exists.
"""

import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy import or_, select

from geonews.db import session_scope
from geonews.ingest.geo import bbox, haversine_km
from geonews.models import Article, Event, Story, UserProfile

RANKER_MODEL = Path(__file__).resolve().parents[2] / "models" / "ranker.txt"
LOCALITY = {"local": 1.0, "regional": 0.7, "national": 0.4, "global": 0.2}
FEATURES = ["recency_h", "distance_km", "in_radius", "locality", "newsworthiness",
            "log_articles", "interest_sim", "follow_match", "topic_ctr", "already_seen"]


@dataclass
class Candidate:
    story: Story
    articles: list[Article]
    features: dict[str, float]
    score: float = 0.0


def _cos(a, b) -> float:
    if a is None or b is None:
        return 0.0
    a, b = np.asarray(a), np.asarray(b)
    d = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / d) if d else 0.0


async def topic_ctr(user_id: int, days: int = 30) -> dict[str, float]:
    """Smoothed per-topic click-through rate from the user's impressions and clicks."""
    since = datetime.now(UTC) - timedelta(days=days)
    async with session_scope() as s:
        rows = await s.execute(
            select(Event.kind, Story.topic).join(Story, Story.id == Event.story_id)
            .where(Event.user_id == user_id, Event.created_at >= since,
                   Event.kind.in_(["impression", "click"]))
        )
        imps, clicks = Counter(), Counter()
        for kind, topic in rows.all():
            (clicks if kind == "click" else imps)[topic] += 1
    prior, k = 0.1, 5  # Beta prior so a single click doesn't dominate
    return {t: (clicks[t] + prior * k) / (imps[t] + k) for t in imps | clicks}


async def seen_story_ids(user_id: int, days: int = 3) -> set[int]:
    since = datetime.now(UTC) - timedelta(days=days)
    async with session_scope() as s:
        rows = await s.execute(
            select(Event.story_id).where(Event.user_id == user_id, Event.created_at >= since,
                                         Event.kind.in_(["impression", "hide"]))
        )
        return {r for r in rows.scalars() if r}


async def candidates(profile: UserProfile, hours: int = 36, limit: int = 200) -> list[Candidate]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    conds = []
    if profile.home_lat is not None:
        lat0, lat1, lon0, lon1 = bbox(profile.home_lat, profile.home_lon, profile.radius_km)
        conds.append(Article.lat.between(lat0, lat1) & Article.lon.between(lon0, lon1))
    conds.append(Article.locality.in_(["national", "global"]) & (Article.newsworthiness >= 0.7))
    async with session_scope() as s:
        stmt = select(Article).where(Article.published_at >= since, or_(*conds))
        arts = list((await s.execute(stmt.limit(limit * 3))).scalars())
        if profile.interest_embedding is not None:
            sim = select(Article).where(Article.published_at >= since).order_by(
                Article.embedding.cosine_distance(profile.interest_embedding)).limit(limit)
            arts += list((await s.execute(sim)).scalars())
        by_story: dict[int, list[Article]] = {}
        for a in arts:
            if a.story_id and a not in by_story.get(a.story_id, []):
                by_story.setdefault(a.story_id, []).append(a)
        stories = (await s.execute(select(Story).where(Story.id.in_(list(by_story))))).scalars()
        return [Candidate(st, by_story[st.id], {}) for st in stories]


def featurize(c: Candidate, profile: UserProfile, ctr: dict[str, float], seen: set[int]) -> None:
    now = datetime.now(UTC)
    newest = max(a.published_at for a in c.articles)
    dist = 5000.0
    if profile.home_lat is not None and c.story.lat is not None:
        dist = haversine_km(profile.home_lat, profile.home_lon, c.story.lat, c.story.lon)
    follows = {f.lower() for f in profile.follows or []}
    ents = {e.lower() for a in c.articles for e in (a.entities or [])}
    c.features = {
        "recency_h": (now - newest).total_seconds() / 3600,
        "distance_km": min(dist, 5000.0),
        "in_radius": float(dist <= profile.radius_km),
        "locality": max(LOCALITY.get(a.locality or "", 0.3) for a in c.articles),
        "newsworthiness": c.story.newsworthiness,
        "log_articles": math.log1p(c.story.article_count),
        "interest_sim": max(_cos(a.embedding, profile.interest_embedding) for a in c.articles),
        "follow_match": float(bool(follows & ents)),
        "topic_ctr": ctr.get(c.story.topic or "", 0.1),
        "already_seen": float(c.story.id in seen),
    }


def heuristic_score(f: dict[str, float]) -> float:
    return (
        1.5 * f["newsworthiness"]
        + 1.0 * f["in_radius"] * f["locality"]
        + 1.2 * f["interest_sim"]
        + 0.8 * f["follow_match"]
        + 0.4 * f["log_articles"]
        + 2.0 * f["topic_ctr"]
        - 0.04 * f["recency_h"]
        - 1.5 * f["already_seen"]
    )


@lru_cache
def _lgbm():
    if not RANKER_MODEL.exists():
        return None
    import lightgbm as lgb

    return lgb.Booster(model_file=str(RANKER_MODEL))


def ranker_version() -> str:
    return "lgbm-v2" if _lgbm() is not None else "heuristic-v1"


async def rank_for_user(profile: UserProfile, k: int = 20) -> list[Candidate]:
    cands = await candidates(profile)
    ctr = await topic_ctr(profile.user_id)
    seen = await seen_story_ids(profile.user_id)
    for c in cands:
        featurize(c, profile, ctr, seen)
    model = _lgbm()
    if model is not None and cands:
        preds = model.predict(np.array([[c.features[f] for f in FEATURES] for c in cands]))
        for c, p in zip(cands, preds, strict=True):
            c.score = float(p)
    else:
        for c in cands:
            c.score = heuristic_score(c.features)
    cands.sort(key=lambda c: -c.score)
    return cands[:k]
