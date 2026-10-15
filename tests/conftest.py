"""Shared fixtures. DB/Redis tests are skipped automatically if the service isn't running."""

import os
import socket

import asyncpg
import pytest
from sqlalchemy.engine import make_url

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5432/inference_test"
)
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


requires_postgres = pytest.mark.skipif(
    not port_open("localhost", 5432), reason="Postgres not running"
)
requires_redis = pytest.mark.skipif(not port_open("localhost", 6379), reason="Redis not running")


async def ensure_database(url: str) -> None:
    """Create the test database if it doesn't exist yet."""
    u = make_url(url)
    conn = await asyncpg.connect(
        user=u.username, password=u.password, host=u.host, port=u.port, database="postgres"
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", u.database)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{u.database}"')
    finally:
        await conn.close()


# ---------- fixtures for the gateway / dispatcher (commit 5) ----------

ALL_TABLES = (
    "run_results, run_metrics, runs, callback_attempts, requests, batches, "
    "model_limit_changes, models"
)


@pytest.fixture
async def redis15():
    """Clean Redis database 15 (test-only)."""
    from backend.db.redis_client import make_redis

    if not port_open("localhost", 6379):
        pytest.skip("Redis not running")
    r = make_redis(TEST_REDIS_URL)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


@pytest.fixture
async def pg_sessionmaker():
    """Migrated, empty test database."""
    import subprocess
    import sys

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    if not port_open("localhost", 5432):
        pytest.skip("Postgres not running")
    await ensure_database(TEST_DATABASE_URL)
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=env,
        capture_output=True,
    )
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {ALL_TABLES} CASCADE"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
