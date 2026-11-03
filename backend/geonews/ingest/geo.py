"""Place extraction + geocoding.

Extraction uses spaCy NER when installed (`uv sync --extra nlp` + `python -m spacy download
en_core_web_sm`), else a capitalized-phrase heuristic. Geocoding hits Nominatim with a Postgres
cache and a global 1 req/s limit, per the Nominatim usage policy.
"""

import asyncio
import math
import re
from functools import lru_cache

import httpx
from sqlalchemy import select

from geonews.config import get_settings
from geonews.db import session_scope
from geonews.models import GeocodeCache

_PLACE_HINT = re.compile(
    r"\b(?:in|at|near|from|across|outside)\s+((?:[A-Z][a-z]+(?:\s|,\s)?){1,3})"
)
_STOP = {"The", "A", "An", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
         "Sunday", "January", "February", "March", "April", "May", "June", "July", "August",
         "September", "October", "November", "December"}
_nominatim_lock = asyncio.Lock()


@lru_cache
def _nlp():
    try:
        import spacy

        return spacy.load("en_core_web_sm", disable=["lemmatizer"])
    except (ImportError, OSError):
        return None


def extract_entities(text: str) -> tuple[list[str], list[str]]:
    """Returns (places, other_entities), most frequent first."""
    nlp = _nlp()
    places: dict[str, int] = {}
    others: dict[str, int] = {}
    if nlp is not None:
        for ent in nlp(text[:5000]).ents:
            name = ent.text.strip()
            if ent.label_ in ("GPE", "LOC", "FAC"):
                places[name] = places.get(name, 0) + 1
            elif ent.label_ in ("ORG", "PERSON"):
                others[name] = others.get(name, 0) + 1
    else:
        for m in _PLACE_HINT.finditer(text[:5000]):
            name = m.group(1).strip(" ,")
            if name.split()[0] not in _STOP:
                places[name] = places.get(name, 0) + 1
    rank = lambda d: [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])][:10]  # noqa: E731
    return rank(places), rank(others)


async def geocode(query: str) -> tuple[float, float] | None:
    key = query.strip().lower()[:300]
    async with session_scope() as s:
        hit = await s.get(GeocodeCache, key)
        if hit is not None:
            return (hit.lat, hit.lon) if hit.lat is not None else None
    settings = get_settings()
    async with _nominatim_lock:
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.get(
                    settings.nominatim_url,
                    params={"q": query, "format": "json", "limit": 1},
                    headers={"User-Agent": settings.nominatim_user_agent},
                )
                r.raise_for_status()
                results = r.json()
            except (httpx.HTTPError, ValueError):
                return None  # don't cache transient failures
        await asyncio.sleep(1.0)
    lat = lon = name = None
    if results:
        lat, lon, name = float(results[0]["lat"]), float(results[0]["lon"]), results[0]["display_name"]
    async with session_scope() as s:
        existing = await s.execute(select(GeocodeCache).where(GeocodeCache.query == key))
        if existing.scalar_one_or_none() is None:
            s.add(GeocodeCache(query=key, lat=lat, lon=lon, display_name=name))
    return (lat, lon) if lat is not None else None


async def locate(places: list[str]) -> tuple[float, float] | None:
    """Geocode the most-mentioned place that resolves."""
    for place in places[:3]:
        point = await geocode(place)
        if point:
            return point
    return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Bounding box for a cheap indexed prefilter before exact haversine."""
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon
