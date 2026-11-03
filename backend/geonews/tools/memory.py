"""User memory tools. The user is taken from request context, never from model input, so the
model cannot read or write another user's memory."""

from pydantic import BaseModel, Field
from sqlalchemy import select

from geonews.db import session_scope
from geonews.llm.router import current_user_id
from geonews.models import AgentRun, UserProfile
from geonews.tools.registry import tool

MAX_MEMORY_CHARS = 2000


class Empty(BaseModel):
    pass


def _uid() -> int:
    uid = current_user_id.get()
    if uid is None:
        raise PermissionError("no user in context")
    return uid


@tool("get_user_profile", "memory", Empty)
async def get_user_profile(_: Empty) -> dict:
    """Get the current user's home location, interests, followed entities, and memory notes."""
    async with session_scope() as s:
        p = await s.get(UserProfile, _uid())
        if p is None:
            return {"error": "no profile"}
        return {"home": p.home_label, "lat": p.home_lat, "lon": p.home_lon,
                "radius_km": p.radius_km, "interests": p.interests, "follows": p.follows,
                "memory": p.memory_summary}


class RememberInput(BaseModel):
    note: str = Field(max_length=300, description="A durable preference, e.g. 'commutes via I-280'.")


@tool("remember_preference", "memory", RememberInput)
async def remember_preference(args: RememberInput) -> dict:
    """Save a durable user preference or fact that should shape future answers and briefings.
    Only save things the user stated about themselves; never save sensitive personal data."""
    async with session_scope() as s:
        p = await s.get(UserProfile, _uid())
        notes = [n for n in (p.memory_summary or "").split("\n") if n]
        if args.note not in notes:
            notes.append(args.note.strip())
        # Keep the newest notes within budget so memory never crowds the context window.
        while len("\n".join(notes)) > MAX_MEMORY_CHARS:
            notes.pop(0)
        p.memory_summary = "\n".join(notes)
    return {"saved": args.note}


class RecentInput(BaseModel):
    limit: int = Field(5, ge=1, le=20)


@tool("recent_interactions", "memory", RecentInput)
async def recent_interactions(args: RecentInput) -> dict:
    """The user's most recent questions and the short answers they got."""
    async with session_scope() as s:
        rows = await s.execute(
            select(AgentRun).where(AgentRun.user_id == _uid(), AgentRun.kind == "chat")
            .order_by(AgentRun.created_at.desc()).limit(args.limit)
        )
        return {"recent": [{"q": r.question, "a": r.answer[:300],
                            "at": r.created_at.isoformat()} for r in rows.scalars()]}
