import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import feedparser
import httpx

from geonews.config import get_settings

log = logging.getLogger(__name__)
UA = {"User-Agent": "GeoNewsBot/0.1 (+https://github.com/; news aggregation for personal use)"}


@dataclass
class RawItem:
    url: str
    title: str
    source: str
    summary: str = ""
    published_at: datetime | None = None

    @property
    def url_hash(self) -> str:
        return url_hash(self.url)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    # Drop query/fragment: tracking params are the main source of duplicate URLs.
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()


def load_feeds(path: str | None = None) -> list[str]:
    p = Path(path or get_settings().feeds_file)
    if not p.exists():
        return []
    return [ln.strip() for ln in p.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


async def fetch_rss(client: httpx.AsyncClient, feed_url: str) -> list[RawItem]:
    try:
        r = await client.get(feed_url, headers=UA, timeout=20, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("feed %s failed: %s", feed_url, e)
        return []
    parsed = feedparser.parse(r.content)
    source = parsed.feed.get("title") or urlsplit(feed_url).netloc
    items = []
    for e in parsed.entries:
        if not e.get("link") or not e.get("title"):
            continue
        published = None
        if e.get("published_parsed"):
            published = datetime(*e.published_parsed[:6], tzinfo=UTC)
        items.append(
            RawItem(
                url=e.link, title=e.title, source=source,
                summary=e.get("summary", ""), published_at=published,
            )
        )
    return items


async def fetch_gdelt(client: httpx.AsyncClient, query: str, timespan: str = "1h") -> list[RawItem]:
    params = {
        "query": query, "mode": "artlist", "format": "json",
        "maxrecords": 75, "timespan": timespan, "sort": "datedesc",
    }
    try:
        r = await client.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=30)
        r.raise_for_status()
        data = r.json() if r.content else {}
    except (httpx.HTTPError, ValueError) as e:
        log.warning("gdelt failed: %s", e)
        return []
    items = []
    for a in data.get("articles", []):
        published = None
        if a.get("seendate"):
            published = datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        items.append(
            RawItem(url=a["url"], title=a.get("title", ""), source=a.get("domain", "gdelt"),
                    published_at=published)
        )
    return items


async def fetch_all() -> list[RawItem]:
    s = get_settings()
    async with httpx.AsyncClient() as client:
        tasks = [fetch_rss(client, f) for f in load_feeds()]
        if s.gdelt_enabled:
            tasks.append(fetch_gdelt(client, s.gdelt_query))
        results = await asyncio.gather(*tasks)
    seen, out = set(), []
    for batch in results:
        for item in batch:
            if item.url_hash not in seen:
                seen.add(item.url_hash)
                out.append(item)
    return out


async def extract_body(client: httpx.AsyncClient, url: str) -> str:
    """Download and extract main article text. Returns "" on failure (we keep the RSS summary)."""
    import trafilatura

    try:
        r = await client.get(url, headers=UA, timeout=20, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError:
        return ""
    text = await asyncio.to_thread(trafilatura.extract, r.text, include_comments=False)
    return text or ""
