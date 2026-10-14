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
