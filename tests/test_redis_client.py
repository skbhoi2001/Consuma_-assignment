import pytest
from redis.asyncio import Redis

from backend.db import keys
from tests.conftest import TEST_REDIS_URL, requires_redis

pytestmark = requires_redis


@pytest.fixture
async def redis():
    r = Redis.from_url(TEST_REDIS_URL, decode_responses=True)  # db 15 = test-only
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


async def test_redis_roundtrip(redis):
    await redis.hset(keys.limits("model-a"), mapping={"rpm": 30000, "tpm": 60000000})
    assert await redis.hgetall(keys.limits("model-a")) == {"rpm": "30000", "tpm": "60000000"}


async def test_get_redis_singleton(monkeypatch):
    import backend.db.redis_client as rc
    from backend.common.settings import get_settings

    monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
    get_settings.cache_clear()
    await rc.close_redis()
    try:
        assert rc.get_redis() is rc.get_redis()
        assert await rc.get_redis().ping()
    finally:
        await rc.close_redis()
        get_settings.cache_clear()
