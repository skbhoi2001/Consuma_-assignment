"""Shared async Redis client.

Uses a *blocking* connection pool: when every connection is busy, callers wait for one
(up to `timeout`) instead of failing with "Too many connections". Under load thousands of
in-flight requests may touch Redis at once, so this matters.
"""

from redis.asyncio import BlockingConnectionPool, Redis

from backend.common.settings import get_settings

_redis: Redis | None = None


def make_redis(url: str, max_connections: int = 100, timeout: float = 20.0) -> Redis:
    pool = BlockingConnectionPool.from_url(
        url, max_connections=max_connections, timeout=timeout, decode_responses=True
    )
    return Redis(connection_pool=pool)


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        s = get_settings()
        _redis = make_redis(s.redis_url, max_connections=s.redis_max_connections)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None
