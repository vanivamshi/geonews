"""Ingestion stages: fetch -> extract -> dedupe/cluster -> geo -> classify -> embed -> store.

Each item is processed independently and idempotently (keyed on url_hash), so a retried job
never double-inserts. Failures are isolated per item; the worker routes repeated failures to a
dead-letter list.
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from geonews.config import get_settings
from geonews.db import session_scope
from geonews.ingest import dedupe, geo
from geonews.ingest.sources import RawItem, extract_body
from geonews.llm.classifier import classify
from geonews.llm.embeddings import embed_one
from geonews.models import Article, Story
from geonews.telemetry import tracer

log = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", " ", text or "").strip()


async def already_ingested(url_hash: str) -> bool:
    async with session_scope() as s:
        found = await s.execute(select(Article.id).where(Article.url_hash == url_hash))
        return found.scalar_one_or_none() is not None


async def recent_signatures() -> list[tuple[int, list[int]]]:
    since = datetime.now(UTC) - timedelta(hours=get_settings().story_window_hours)
    async with session_scope() as s:
        rows = await s.execute(
            select(Article.story_id, Article.minhash).where(
                Article.ingested_at >= since, Article.minhash.is_not(None),
                Article.story_id.is_not(None),
            )
        )
        return [(sid, mh) for sid, mh in rows.all()]


async def process_item(item: RawItem, client: httpx.AsyncClient) -> int | None:
    """Returns the new article id, or None if skipped."""
    with tracer.start_as_current_span("ingest.item") as span:
        span.set_attribute("url", item.url)
        if await already_ingested(item.url_hash):
            return None

        summary = _strip_html(item.summary)
        body = await extract_body(client, item.url) or summary
        text = dedupe.dedupe_text(item.title, summary, body)
        sig = dedupe.signature(text)
        story_id, sim = dedupe.best_match(
            sig, await recent_signatures(), get_settings().dedupe_threshold
        )

        places, entities = geo.extract_entities(f"{item.title}. {body}")
        point = await geo.locate(places)
        labels = await classify(item.title, body)
        embedding = await embed_one(f"{item.title}\n{summary or body[:500]}")

        async with session_scope() as s:
            if story_id is None:
                story = Story(
                    title=item.title, topic=labels["topic"],
                    newsworthiness=labels["newsworthiness"],
                    lat=point[0] if point else None, lon=point[1] if point else None,
                )
                s.add(story)
                await s.flush()
                story_id = story.id
            else:
                story = await s.get(Story, story_id)
                story.article_count += 1
                story.last_updated = datetime.now(UTC)
                story.newsworthiness = max(story.newsworthiness, labels["newsworthiness"])
                if story.lat is None and point:
                    story.lat, story.lon = point
            article = Article(
                url_hash=item.url_hash, url=item.url, source=item.source[:200],
                title=item.title, summary=summary[:2000], body=body,
                published_at=item.published_at or datetime.now(UTC),
                story_id=story_id, minhash=sig, places=places, entities=entities,
                lat=point[0] if point else None, lon=point[1] if point else None,
                topic=labels["topic"], locality=labels["locality"],
                newsworthiness=labels["newsworthiness"],
                classifier_version=labels["classifier_version"], embedding=embedding,
            )
            s.add(article)
            await s.flush()
            span.set_attribute("story_id", story_id)
            span.set_attribute("dedupe_similarity", sim)
            return article.id
