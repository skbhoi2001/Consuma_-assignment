import httpx
import pytest

from backend.simulator.app import create_app
from backend.simulator.config import SimModelConfig


@pytest.fixture
async def client():
    models = [
        SimModelConfig(name="fast", rpm=3, tpm=10_000, latency_median_ms=0),
        SimModelConfig(
            name="broken", rpm=100, tpm=10_000, latency_median_ms=0, permanent_error_rate=1.0
        ),
    ]
    app = create_app(models, seed=1)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sim") as c:
        yield c


def call(model="fast", rid="r1", tokens=100):
    return {"request_id": rid, "model": model, "estimated_tokens": tokens}


async def test_success(client):
    r = await client.post("/sim/inference", json=call())
    assert r.status_code == 200
    body = r.json()
    assert body["request_id"] == "r1" and 90 <= body["actual_tokens"] <= 110


async def test_rate_limited_after_rpm(client):
    codes = [
        (await client.post("/sim/inference", json=call(rid=f"r{i}"))).status_code for i in range(4)
    ]
    assert codes == [200, 200, 200, 429]
    r = await client.post("/sim/inference", json=call(rid="r9"))
    assert r.json()["error"] == "rate_limited" and int(r.headers["Retry-After"]) >= 1


async def test_permanent_error_and_unknown_model(client):
    assert (await client.post("/sim/inference", json=call(model="broken"))).status_code == 400
    assert (await client.post("/sim/inference", json=call(model="nope"))).status_code == 404


async def test_bad_input_is_422(client):
    assert (await client.post("/sim/inference", json=call(tokens=0))).status_code == 422


async def test_update_stats_reset(client):
    r = await client.put("/sim/models/fast", json={"rpm": 10, "transient_error_rate": 0.0})
    assert r.status_code == 200 and r.json()["rpm"] == 10
    assert (await client.put("/sim/models/nope", json={"rpm": 1})).status_code == 404
    bad = {"transient_error_rate": 0.9}
    assert (await client.put("/sim/models/broken", json=bad)).status_code == 422

    await client.post("/sim/inference", json=call())
    stats = (await client.get("/sim/stats")).json()
    assert stats["fast"]["accepted"] == 1 and stats["fast"]["rpm"] == 10

    assert (await client.post("/sim/reset")).json() == {"status": "reset"}
    assert (await client.get("/sim/stats")).json()["fast"]["accepted"] == 0


async def test_models_and_health(client):
    names = [m["name"] for m in (await client.get("/sim/models")).json()]
    assert names == ["fast", "broken"]
    assert (await client.get("/sim/models/fast")).json()["rpm"] == 3
    assert (await client.get("/healthz")).json() == {"status": "ok"}
