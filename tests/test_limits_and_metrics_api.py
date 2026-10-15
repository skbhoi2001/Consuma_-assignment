"""Runtime limit changes (Scenario 2 mechanics) and the metrics endpoints."""

import json

import httpx
import pytest

from backend.dispatcher.provider_client import InProcessProviderClient
from backend.gateway.app import create_app
from backend.gateway.runtime import Runtime
from backend.simulator.config import SimModelConfig
from backend.simulator.provider import ProviderSimulator
from tests.fakes import make_settings, models, wait_until


@pytest.fixture
async def env(redis15, pg_sessionmaker):
    sim = ProviderSimulator(
        [
            SimModelConfig(name=n, rpm=10**6, tpm=10**9, latency_median_ms=0, enforce_limits=False)
            for n in ("model-a", "model-b")
        ]
    )
    rt = Runtime(
        make_settings(),
        models=models(("model-a", 600, 10**8), ("model-b", 600, 10**8)),
        provider=InProcessProviderClient(sim),
        redis=redis15,
        sessionmaker=pg_sessionmaker,
        callback_http=httpx.AsyncClient(),
    )
    await rt.start()
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime=rt)), base_url="http://gw"
    )
    yield rt, client, sim
    await client.aclose()
    await rt.stop()


async def test_list_and_get_models(env):
    _, client, _ = env
    names = [m["name"] for m in (await client.get("/v1/models")).json()]
    assert names == ["model-a", "model-b"]
    a = (await client.get("/v1/models/model-a")).json()
    assert a["rpm"] == 600 and a["effective_rpm"] == 600
    assert (await client.get("/v1/models/nope")).status_code == 404


async def test_patch_limits_applies_live_persists_and_syncs_simulator(env):
    rt, client, sim = env
    r = await client.patch("/v1/models/model-a/limits", json={"rpm": 120})
    assert r.status_code == 200
    body = r.json()
    assert body["rpm"] == 120 and body["tpm"] == 10**8 and body["simulator_synced"] is True
    assert rt.registry.get("model-a").rpm == 120
    assert sim.models["model-a"].rpm == 120
    assert rt.dispatcher.models["model-b"].rpm == 600  # other model untouched

    history = (await client.get("/v1/models/model-a/limits/history")).json()
    assert history[0]["source"] == "api"
    assert (history[0]["old_rpm"], history[0]["new_rpm"]) == (600, 120)


async def test_patch_validation(env):
    _, client, _ = env
    assert (await client.patch("/v1/models/model-a/limits", json={})).status_code == 422
    assert (await client.patch("/v1/models/model-a/limits", json={"rpm": 0})).status_code == 422
    assert (await client.patch("/v1/models/nope/limits", json={"rpm": 5})).status_code == 404


async def test_lowered_limit_throttles_only_that_model(env):
    """Cut model-a to 2 RPM while both models have work queued: a stalls, b drains."""
    rt, client, _ = env
    await client.patch("/v1/models/model-a/limits", json={"rpm": 2})
    reqs = [{"model": m, "estimated_tokens": 10} for m in ("model-a", "model-b") for _ in range(10)]
    await client.post("/v1/batches", json={"requests": reqs})

    async def b_done():
        live = (await client.get("/v1/metrics/live")).json()["models"]
        return live["model-b"]["rolling_60s_requests"] == 10

    await wait_until(b_done, 5)
    live = (await client.get("/v1/metrics/live")).json()["models"]
    assert live["model-a"]["rolling_60s_requests"] == 2
    assert live["model-a"]["queued"] == 8

    await client.patch("/v1/models/model-a/limits", json={"rpm": 600})  # restore

    async def a_done():
        live = (await client.get("/v1/metrics/live")).json()["models"]
        return live["model-a"]["rolling_60s_requests"] == 10

    await wait_until(a_done, 5)


async def test_metrics_endpoints(env):
    rt, client, _ = env
    await client.post(
        "/v1/batches", json={"requests": [{"model": "model-a", "estimated_tokens": 5}] * 20}
    )

    async def all_done():
        acc = (await client.get("/v1/metrics/live")).json()["accounting"]
        return acc["succeeded"] == 20

    await wait_until(all_done, 5)
    live = (await client.get("/v1/metrics/live")).json()
    assert live["accounting"]["balanced"]
    assert live["models"]["model-a"]["latency_p50_ms"] is not None

    ts = (await client.get("/v1/metrics/timeseries", params={"model": "model-a"})).json()
    assert sum(r["dispatched"] for r in ts["rows"]) == 20
    assert sum(r["tokens"] for r in ts["rows"]) == 100

    lat = (await client.get("/v1/metrics/latency")).json()
    assert lat["count"] == 20

    prom = (await client.get("/metrics")).text
    assert 'gateway_final_requests_total{model="model-a",state="succeeded"} 20.0' in prom

    health = (await client.get("/healthz")).json()
    assert health["pid"] == rt.pid and health["uptime_seconds"] >= 0


async def test_sse_stream(env):
    _, client, _ = env
    async with client.stream("GET", "/v1/stream", params={"interval": 0.01, "max_events": 2}) as r:
        data = [line[5:].strip() async for line in r.aiter_lines() if line.startswith("data:")]
    assert len(data) == 2
    assert "accounting" in json.loads(data[0])


async def test_batch_list_and_callback_attempts(env):
    _, client, _ = env
    bid = (
        await client.post(
            "/v1/batches", json={"requests": [{"model": "model-b", "estimated_tokens": 1}]}
        )
    ).json()["batch_id"]
    listed = (await client.get("/v1/batches")).json()
    assert listed[0]["batch_id"] == bid
    assert (await client.get(f"/v1/batches/{bid}/callbacks")).json() == []


async def test_limit_changes_never_put_us_above_the_provider(env):
    """Decrease: our limiter drops before the provider is told. Increase: the provider is
    raised before our limiter. Either way ours is never higher than the provider's."""
    rt, client, _ = env
    seen = []
    original = rt.sync_simulator_limits

    async def spy(model, rpm, tpm):
        seen.append(rt.registry.get(model).rpm)  # our limit at the moment the provider changes
        return await original(model, rpm, tpm)

    rt.sync_simulator_limits = spy
    await client.patch("/v1/models/model-a/limits", json={"rpm": 100})  # decrease
    await client.patch("/v1/models/model-a/limits", json={"rpm": 900})  # increase
    assert seen == [100, 100]  # already lowered / not yet raised
    assert rt.registry.get("model-a").rpm == 900
