from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from geonews.config import get_settings
from geonews.db import Base

DIM = get_settings().embedding_dim


def now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    profile: Mapped["UserProfile"] = relationship(back_populates="user", uselist=False)


class UserProfile(Base):
    __tablename__ = "user_profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    home_label: Mapped[str | None] = mapped_column(String(200))
    home_lat: Mapped[float | None] = mapped_column(Float)
    home_lon: Mapped[float | None] = mapped_column(Float)
    radius_km: Mapped[float] = mapped_column(Float, default=50.0)
    interests: Mapped[list[str]] = mapped_column(JSON, default=list)  # free-text topics
    follows: Mapped[list[str]] = mapped_column(JSON, default=list)  # entity names
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    channels: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["web"])
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    interest_embedding: Mapped[list[float] | None] = mapped_column(Vector(DIM))
    memory_summary: Mapped[str] = mapped_column(Text, default="")  # agent-maintained notes
    user: Mapped[User] = relationship(back_populates="profile")


class Story(Base):
    """A cluster of near-duplicate articles about the same event."""

    __tablename__ = "stories"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    article_count: Mapped[int] = mapped_column(Integer, default=1)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    topic: Mapped[str | None] = mapped_column(String(64))
    newsworthiness: Mapped[float] = mapped_column(Float, default=0.5)
    articles: Mapped[list["Article"]] = relationship(back_populates="story")


class Article(Base):
    __tablename__ = "articles"
    id: Mapped[int] = mapped_column(primary_key=True)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("stories.id"), index=True)
    minhash: Mapped[list[int] | None] = mapped_column(JSON)
    places: Mapped[list[str]] = mapped_column(JSON, default=list)
    entities: Mapped[list[str]] = mapped_column(JSON, default=list)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    topic: Mapped[str | None] = mapped_column(String(64))
    locality: Mapped[str | None] = mapped_column(String(16))  # local | regional | national | global
    newsworthiness: Mapped[float] = mapped_column(Float, default=0.5)
    classifier_version: Mapped[str | None] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(DIM))
    story: Mapped[Story | None] = relationship(back_populates="articles")

    __table_args__ = (Index("articles_lat_lon", "lat", "lon"),)


class GeocodeCache(Base):
    __tablename__ = "geocode_cache"
    query: Mapped[str] = mapped_column(String(300), primary_key=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    display_name: Mapped[str | None] = mapped_column(Text)


class Event(Base):
    """Append-only user behavior log. Partition by day once volume warrants it."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # impression|click|dwell|rating|query|hide
    story_id: Mapped[int | None] = mapped_column(ForeignKey("stories.id"))
    briefing_id: Mapped[int | None] = mapped_column(ForeignKey("briefings.id"))
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"))
    value: Mapped[float | None] = mapped_column(Float)  # dwell seconds, rating +1/-1
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Briefing(Base):
    __tablename__ = "briefings"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    body_markdown: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    items: Mapped[list["BriefingItem"]] = relationship(back_populates="briefing")


class BriefingItem(Base):
    """What was shown, with the ranking features at serve time (for offline ranker training)."""

    __tablename__ = "briefing_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    briefing_id: Mapped[int] = mapped_column(ForeignKey("briefings.id"), index=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id"))
    position: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    features: Mapped[dict] = mapped_column(JSON)
    ranker_version: Mapped[str] = mapped_column(String(32))
    briefing: Mapped[Briefing] = relationship(back_populates="items")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # chat | briefing | eval
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list[dict]] = mapped_column(JSON, default=list)
    verdict: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running")
    steps: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    tool_calls: Mapped[list["ToolCall"]] = relationship(
        back_populates="run", order_by="ToolCall.id"
    )


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    agent: Mapped[str] = mapped_column(String(32))  # orchestrator | retriever | researcher | ...
    tool: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict] = mapped_column(JSON)
    result_preview: Mapped[str] = mapped_column(Text)
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    run: Mapped[AgentRun] = relationship(back_populates="tool_calls")


class LLMCall(Base):
    """Every model call, for cost/latency dashboards and per-task routing decisions."""

    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer)
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    suite: Mapped[str] = mapped_column(String(64))
    git_sha: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvalScore(Base):
    __tablename__ = "eval_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    eval_run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(128))
    metric: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Float)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Label(Base):
    """Human labels from the labeling UI: relevance labels and judge-agreement labels."""

    __tablename__ = "labels"
    id: Mapped[int] = mapped_column(primary_key=True)
    labeler_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32))  # relevance | faithfulness | usefulness
    target_type: Mapped[str] = mapped_column(String(32))  # article | agent_run
    target_id: Mapped[int] = mapped_column(Integer)
    value: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("labeler_id", "kind", "target_type", "target_id"),)


class Preference(Base):
    """Pairwise preference (chosen vs rejected) for DPO."""

    __tablename__ = "preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    labeler_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    prompt: Mapped[str] = mapped_column(Text)
    chosen: Mapped[str] = mapped_column(Text)
    rejected: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))  # labeling_ui | thumbs
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
