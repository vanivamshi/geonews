import asyncio
import json
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from geonews.agents.orchestrator import answer_question
from geonews.api.deps import current_user
from geonews.cache import rate_limit
from geonews.db import get_session
from geonews.feed.briefing import latest_briefing, write_briefing
from geonews.feed.ranking import rank_for_user, ranker_version
from geonews.ingest.geo import geocode
from geonews.llm.embeddings import embed_one
from geonews.models import AgentRun, Article, Briefing, Event, Story, User, UserProfile

router = APIRouter()


class SignupIn(BaseModel):
    email: EmailStr


@router.post("/auth/signup")
async def signup(body: SignupIn, session: AsyncSession = Depends(get_session)):
    """v0 auth: email -> bearer token (shown once). Replace with magic links before going public."""
    if not await rate_limit("signup", limit=20, window_s=3600):
        raise HTTPException(429, "too many signups")
    existing = (await session.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "email already registered")
    user = User(email=body.email, token=secrets.token_urlsafe(32))
    session.add(user)
    await session.flush()
    session.add(UserProfile(user_id=user.id, interests=[], follows=[], channels=["web"]))
    return {"user_id": user.id, "token": user.token}


class ProfileIn(BaseModel):
    home_label: str | None = None
    radius_km: float = Field(50, ge=1, le=1000)
    interests: list[str] = Field(default_factory=list, max_length=20)
    follows: list[str] = Field(default_factory=list, max_length=50)
    timezone: str = "UTC"
    channels: list[Literal["web", "email", "telegram"]] = Field(default_factory=lambda: ["web"])
    telegram_chat_id: str | None = None


def _profile_out(p: UserProfile) -> dict:
    return {"home_label": p.home_label, "home_lat": p.home_lat, "home_lon": p.home_lon,
            "radius_km": p.radius_km, "interests": p.interests, "follows": p.follows,
            "timezone": p.timezone, "channels": p.channels, "memory": p.memory_summary}


@router.get("/me/profile")
async def get_profile(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return _profile_out(await session.get(UserProfile, user.id))


@router.put("/me/profile")
async def put_profile(body: ProfileIn, user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    p = await session.get(UserProfile, user.id)
    if body.home_label and body.home_label != p.home_label:
        point = await geocode(body.home_label)
        if point is None:
            raise HTTPException(422, f"could not find '{body.home_label}'")
        p.home_lat, p.home_lon = point
    for field in ("home_label", "radius_km", "interests", "follows", "timezone", "channels",
                  "telegram_chat_id"):
        setattr(p, field, getattr(body, field))
    p.interest_embedding = (
        await embed_one("; ".join(body.interests + body.follows)) if body.interests or body.follows
        else None
    )
    return _profile_out(p)


@router.delete("/me/memory")
async def clear_memory(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    (await session.get(UserProfile, user.id)).memory_summary = ""
    return {"ok": True}


@router.get("/feed")
async def feed(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    profile = await session.get(UserProfile, user.id)
    ranked = await rank_for_user(profile, k=30)
    out = []
    for pos, c in enumerate(ranked):
        session.add(Event(user_id=user.id, kind="impression", story_id=c.story.id,
                          meta={"position": pos, "surface": "feed", "score": c.score,
                                "features": c.features, "ranker": ranker_version()}))
        top = max(c.articles, key=lambda a: a.published_at)
        out.append({
            "story_id": c.story.id, "title": c.story.title, "topic": c.story.topic,
            "article_count": c.story.article_count, "score": round(c.score, 3),
            "distance_km": round(c.features["distance_km"], 1),
            "top_article": {"id": f"A{top.id}", "source": top.source, "url": top.url,
                            "summary": top.summary[:400], "published_at": top.published_at.isoformat()},
        })
    return {"ranker": ranker_version(), "items": out}


@router.get("/stories/{story_id}")
async def story(story_id: int, user: User = Depends(current_user),
                session: AsyncSession = Depends(get_session)):
    st = await session.get(Story, story_id)
    if st is None:
        raise HTTPException(404)
    arts = (await session.execute(
        select(Article).where(Article.story_id == story_id).order_by(Article.published_at)
    )).scalars()
    session.add(Event(user_id=user.id, kind="click", story_id=story_id, meta={"surface": "story"}))
    return {"story_id": st.id, "title": st.title, "topic": st.topic, "lat": st.lat, "lon": st.lon,
            "articles": [{"id": f"A{a.id}", "title": a.title, "source": a.source, "url": a.url,
                          "published_at": a.published_at.isoformat(), "summary": a.summary[:600]}
                         for a in arts]}


@router.get("/articles/{article_id}")
async def article(article_id: str, user: User = Depends(current_user),
                  session: AsyncSession = Depends(get_session)):
    a = await session.get(Article, int(article_id.lstrip("Aa")))
    if a is None:
        raise HTTPException(404)
    session.add(Event(user_id=user.id, kind="click", story_id=a.story_id, meta={"surface": "citation"}))
    return {"id": f"A{a.id}", "title": a.title, "source": a.source, "url": a.url,
            "published_at": a.published_at.isoformat(), "story_id": a.story_id,
            "summary": a.summary[:800], "places": a.places}


@router.get("/briefing/latest")
async def briefing_latest(user: User = Depends(current_user)):
    return await latest_briefing(user.id) or {"body": None}


@router.post("/briefing/generate")
async def briefing_generate(user: User = Depends(current_user)):
    if not await rate_limit(f"brief:{user.id}", limit=3, window_s=86400):
        raise HTTPException(429, "briefing limit reached for today")
    return await write_briefing(user.id)


class ChatIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


@router.post("/chat")
async def chat(body: ChatIn, user: User = Depends(current_user)):
    """Server-sent events: run, tool_call, subagent_start/done, skill, answer_delta, verifying,
    revising, final, error."""
    if not await rate_limit(f"chat:{user.id}", limit=30, window_s=3600):
        raise HTTPException(429, "chat limit reached, try again later")
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(kind: str, data: dict) -> None:
        await queue.put((kind, data))

    async def run() -> None:
        try:
            await answer_question(body.question, user_id=user.id, emit=emit)
        except Exception as e:
            await queue.put(("error", {"message": f"{type(e).__name__}"}))
        finally:
            await queue.put(None)

    async def stream():
        task = asyncio.create_task(run())
        try:
            while (item := await queue.get()) is not None:
                kind, data = item
                yield {"event": kind, "data": json.dumps(data, default=str)}
        finally:
            if not task.done():
                task.cancel()

    return EventSourceResponse(stream())


class EventIn(BaseModel):
    kind: Literal["click", "dwell", "rating", "hide"]
    story_id: int | None = None
    briefing_id: int | None = None
    agent_run_id: int | None = None
    value: float | None = None
    meta: dict = Field(default_factory=dict)


@router.post("/events")
async def log_event(body: EventIn, user: User = Depends(current_user),
                    session: AsyncSession = Depends(get_session)):
    if body.agent_run_id is not None:
        run = await session.get(AgentRun, body.agent_run_id)
        if run is None or run.user_id != user.id:
            raise HTTPException(404)
    session.add(Event(user_id=user.id, **body.model_dump()))
    return {"ok": True}


@router.get("/r/{briefing_id}/{article_id}")
async def tracked_redirect(briefing_id: int, article_id: str,
                           session: AsyncSession = Depends(get_session)):
    """Click-tracking redirect used in email/Telegram briefings."""
    art = await session.get(Article, int(article_id.lstrip("A")))
    if art is None:
        raise HTTPException(404)
    b = await session.get(Briefing, briefing_id)
    if b is not None:
        session.add(Event(user_id=b.user_id, kind="click", story_id=art.story_id,
                          briefing_id=briefing_id, meta={"surface": "briefing_link"}))
    return RedirectResponse(art.url, status_code=302)
