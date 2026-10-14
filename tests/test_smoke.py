import os
import socket
import sys

import pytest


def test_python_version():
    assert sys.version_info >= (3, 12)


def test_backend_importable():
    import backend  # noqa: F401


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _port_open("localhost", 6379), reason="Redis not running")
def test_redis_reachable():
    import redis

    r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    assert r.ping()


@pytest.mark.skipif(not _port_open("localhost", 5432), reason="Postgres not running")
def test_postgres_reachable():
    assert _port_open("localhost", 5432)
