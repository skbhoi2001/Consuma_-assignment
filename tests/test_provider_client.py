"""HttpProviderClient against the real simulator app served on a local port."""

import asyncio
import socket

import pytest
import uvicorn

from backend.dispatcher.job import Job
from backend.dispatcher.provider_client import HttpProviderClient, classify
from backend.simulator.app import create_app
from backend.simulator.config import SimModelConfig


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def sim_url():
    models = [
        SimModelConfig(name="ok", rpm=2, tpm=10**6, latency_median_ms=0),
        SimModelConfig(name="bad", rpm=100, tpm=10**6, latency_median_ms=0, permanent_error_rate=1),
        SimModelConfig(
            name="flaky", rpm=100, tpm=10**6, latency_median_ms=0, transient_error_rate=1
        ),
    ]
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(models, seed=1), port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task


def job(model, rid="r1"):
    return Job(request_id=rid, model=model, estimated_tokens=100, submitted_at=0, deadline=1)


def test_classify():
    assert [classify(c) for c in (200, 429, 503, 400, 404)] == [
        "success",
        "transient",
        "transient",
        "permanent",
        "permanent",
    ]


async def test_outcomes_over_http(sim_url):
    client = HttpProviderClient(sim_url)
    try:
        ok = await client.call(job("ok"))
        assert ok.outcome == "success" and 90 <= ok.actual_tokens <= 110
        assert (await client.call(job("bad"))).outcome == "permanent"
        assert (await client.call(job("flaky"))).outcome == "transient"
        await client.call(job("ok", "r2"))
        limited = await client.call(job("ok", "r3"))  # 3rd call within a minute at rpm=2
        assert limited.outcome == "transient" and limited.status_code == 429
        assert limited.retry_after and limited.retry_after > 0
    finally:
        await client.aclose()


async def test_connection_refused_is_transient():
    client = HttpProviderClient(f"http://127.0.0.1:{free_port()}", timeout=2)
    try:
        result = await client.call(job("ok"))
        assert result.outcome == "transient" and "connection error" in result.error
    finally:
        await client.aclose()
