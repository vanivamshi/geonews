import json
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis

from geonews.config import get_settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


async def cached_json(key: str, ttl: int, produce: Callable[[], Awaitable[Any]]) -> Any:
    r = get_redis()
    hit = await r.get(key)
    if hit is not None:
        return json.loads(hit)
    value = await produce()
    await r.set(key, json.dumps(value, default=str), ex=ttl)
    return value


async def rate_limit(key: str, limit: int, window_s: int) -> bool:
    """Fixed-window limiter. Returns True if the call is allowed."""
    r = get_redis()
    bucket = f"rl:{key}"
    count = await r.incr(bucket)
    if count == 1:
        await r.expire(bucket, window_s)
    return count <= limit


async def add_user_spend(user_id: int, usd: float) -> float:
    """Track per-user daily LLM spend for the cost cap guardrail."""
    from datetime import UTC, datetime

    key = f"spend:{user_id}:{datetime.now(UTC):%Y%m%d}"
    r = get_redis()
    total = await r.incrbyfloat(key, usd)
    await r.expire(key, 2 * 86400)
    return float(total)


async def get_user_spend(user_id: int) -> float:
    from datetime import UTC, datetime

    value = await get_redis().get(f"spend:{user_id}:{datetime.now(UTC):%Y%m%d}")
    return float(value or 0.0)
