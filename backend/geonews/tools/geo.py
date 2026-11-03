from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import select

from geonews.db import session_scope
from geonews.ingest.geo import bbox, geocode, haversine_km
from geonews.models import Story
from geonews.tools.registry import tool


class GeocodeInput(BaseModel):
    place: str


@tool("geocode", "geo", GeocodeInput)
async def geocode_place(args: GeocodeInput) -> dict:
    """Resolve a place name to latitude/longitude."""
    point = await geocode(args.place)
    if point is None:
        return {"error": f"Could not geocode '{args.place}'"}
    return {"place": args.place, "lat": point[0], "lon": point[1]}


class StoriesNearInput(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_km: float = Field(25, ge=1, le=500)
    since_hours: int = Field(48, ge=1, le=24 * 14)
    limit: int = Field(10, ge=1, le=30)


@tool("stories_near", "geo", StoriesNearInput)
async def stories_near(args: StoriesNearInput) -> dict:
    """List the most newsworthy recent stories within a radius of a point, nearest-weighted.
    Use for 'what is happening around X' questions where there is no specific topic."""
    since = datetime.now(UTC) - timedelta(hours=args.since_hours)
    lat0, lat1, lon0, lon1 = bbox(args.lat, args.lon, args.radius_km)
    async with session_scope() as s:
        rows = await s.execute(
            select(Story).where(
                Story.last_updated >= since,
                Story.lat.between(lat0, lat1), Story.lon.between(lon0, lon1),
            ).limit(300)
        )
        out = []
        for st in rows.scalars():
            d = haversine_km(args.lat, args.lon, st.lat, st.lon)
            if d <= args.radius_km:
                score = st.newsworthiness * (1 + st.article_count**0.5) / (1 + d / 10)
                out.append({"story_id": st.id, "title": st.title, "topic": st.topic,
                            "distance_km": round(d, 1), "article_count": st.article_count,
                            "score": round(score, 3)})
    out.sort(key=lambda x: -x["score"])
    return {"stories": out[: args.limit]}
