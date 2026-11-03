from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import select

from geonews.db import session_scope
from geonews.ingest.geo import bbox, geocode, haversine_km
from geonews.llm.embeddings import embed_one
from geonews.models import Article, Story
from geonews.tools.registry import tool


class SearchStoriesInput(BaseModel):
    query: str = Field(description="Natural-language search query.")
    place: str | None = Field(None, description="Optional place name to restrict results near.")
    radius_km: float = Field(50, ge=1, le=2000)
    since_hours: int = Field(72, ge=1, le=24 * 30)
    topic: str | None = Field(None, description="Optional topic filter, e.g. transport.")
    limit: int = Field(8, ge=1, le=20)


@tool("search_stories", "news", SearchStoriesInput)
async def search_stories(args: SearchStoriesInput) -> dict:
    """Semantic search over recent news stories, optionally near a place and within a time window.
    Returns stories with their article ids (use these ids as citations, e.g. [A123]) and a short
    summary per article. Use get_article to read full text before relying on specific details."""
    qvec = await embed_one(args.query)
    since = datetime.now(UTC) - timedelta(hours=args.since_hours)
    distance = Article.embedding.cosine_distance(qvec).label("distance")
    stmt = select(Article, distance).where(Article.published_at >= since)
    center = None
    if args.place:
        center = await geocode(args.place)
        if center is None:
            return {"error": f"Could not geocode '{args.place}'. Try a larger nearby city."}
        lat0, lat1, lon0, lon1 = bbox(*center, args.radius_km)
        stmt = stmt.where(Article.lat.between(lat0, lat1), Article.lon.between(lon0, lon1))
    if args.topic:
        stmt = stmt.where(Article.topic == args.topic)
    stmt = stmt.order_by(distance).limit(args.limit * 5)

    async with session_scope() as s:
        rows = (await s.execute(stmt)).all()
        stories: dict[int, dict] = {}
        for art, dist in rows:
            if center and art.lat is not None:
                if haversine_km(*center, art.lat, art.lon) > args.radius_km:
                    continue
            entry = stories.setdefault(
                art.story_id, {"story_id": art.story_id, "relevance": round(1 - dist, 3), "articles": []}
            )
            if len(entry["articles"]) < 3:
                entry["articles"].append({
                    "id": f"A{art.id}", "title": art.title, "source": art.source,
                    "published_at": art.published_at.isoformat(), "summary": art.summary[:300],
                    "places": art.places[:3],
                })
        ids = list(stories)[: args.limit]
        if ids:
            meta = await s.execute(select(Story).where(Story.id.in_(ids)))
            for st in meta.scalars():
                stories[st.id].update(title=st.title, article_count=st.article_count, topic=st.topic)
    return {"stories": [stories[i] for i in ids], "searched_near": args.place}


class GetArticleInput(BaseModel):
    article_id: str = Field(description="Article id such as 'A123'.")
    max_chars: int = Field(6000, ge=500, le=20000)


@tool("get_article", "news", GetArticleInput)
async def get_article(args: GetArticleInput) -> dict:
    """Fetch the full extracted text and metadata of one article by id (e.g. 'A123')."""
    aid = int(args.article_id.lstrip("Aa"))
    async with session_scope() as s:
        art = await s.get(Article, aid)
        if art is None:
            return {"error": f"No article {args.article_id}"}
        return {
            "id": f"A{art.id}", "title": art.title, "source": art.source, "url": art.url,
            "published_at": art.published_at.isoformat(), "story_id": art.story_id,
            "places": art.places, "entities": art.entities, "text": art.body[: args.max_chars],
        }


class StoryTimelineInput(BaseModel):
    story_id: int


@tool("get_story_timeline", "news", StoryTimelineInput)
async def get_story_timeline(args: StoryTimelineInput) -> dict:
    """List all articles in a story cluster in chronological order, to see how coverage evolved."""
    async with session_scope() as s:
        story = await s.get(Story, args.story_id)
        if story is None:
            return {"error": f"No story {args.story_id}"}
        rows = await s.execute(
            select(Article).where(Article.story_id == args.story_id).order_by(Article.published_at)
        )
        return {
            "story_id": story.id, "title": story.title,
            "timeline": [
                {"id": f"A{a.id}", "published_at": a.published_at.isoformat(),
                 "source": a.source, "title": a.title}
                for a in rows.scalars()
            ],
        }
