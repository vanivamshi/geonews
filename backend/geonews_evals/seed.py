"""Load the synthetic eval corpus (fictional towns) into the database.

Fixture articles get a fixed source prefix so they can be removed, and their place names are
pre-seeded into the geocode cache so evals never call Nominatim.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from geonews.db import init_db, session_scope
from geonews.ingest.dedupe import signature
from geonews.llm.embeddings import embed
from geonews.models import Article, GeocodeCache, Story

FIXTURES = Path(__file__).parent / "datasets" / "fixtures.json"
SOURCE_TAG = "[eval] "


async def seed() -> dict[str, str]:
    """Returns fixture key -> article id ('A123')."""
    await init_db()
    data = json.loads(FIXTURES.read_text())
    now = datetime.now(UTC)
    async with session_scope() as s:
        old = select(Article.story_id).where(Article.source.startswith(SOURCE_TAG))
        story_ids = list((await s.execute(old)).scalars())
        await s.execute(delete(Article).where(Article.source.startswith(SOURCE_TAG)))
        await s.execute(delete(Story).where(Story.id.in_(story_ids)))
        for g in data["geocode"]:
            if await s.get(GeocodeCache, g["query"]) is None:
                s.add(GeocodeCache(**g))

    arts = data["articles"]
    vectors = await embed([f"{a['title']}\n{a['body'][:500]}" for a in arts])
    keymap: dict[str, str] = {}
    stories: dict[str, int] = {}
    async with session_scope() as s:
        for a, vec in zip(arts, vectors, strict=True):
            published = now - timedelta(hours=a["hours_ago"])
            if a["story"] not in stories:
                st = Story(title=a["title"], lat=a["lat"], lon=a["lon"], newsworthiness=0.7,
                           first_seen=published, last_updated=published, article_count=0)
                s.add(st)
                await s.flush()
                stories[a["story"]] = st.id
            st = await s.get(Story, stories[a["story"]])
            st.article_count += 1
            if published > st.last_updated:
                st.last_updated, st.title = published, a["title"]
            art = Article(
                url_hash=f"eval-{a['key']}", url=f"https://example.invalid/eval/{a['key']}",
                source=SOURCE_TAG + a["source"], title=a["title"], summary=a["body"][:300],
                body=a["body"], published_at=published, story_id=st.id,
                minhash=signature(a["title"] + " " + a["body"]), places=[], entities=[],
                lat=a["lat"], lon=a["lon"], locality="local", newsworthiness=0.7,
                classifier_version="fixture", embedding=vec,
            )
            s.add(art)
            await s.flush()
            keymap[a["key"]] = f"A{art.id}"
    return keymap
