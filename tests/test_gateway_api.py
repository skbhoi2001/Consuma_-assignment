"""HTTP API: single requests, batches, callbacks (Scenario 3 shape), stats."""

import time

import httpx
import pytest
from sqlalchemy import select

from backend.db.tables import CallbackAttempt
from backend.dispatcher.provider_client import InProcessProviderClient
from backend.gateway.app import create_app
from backend.gateway.runtime import Runtime
from backend.receiver.app import create_app as create_receiver
from backend.simulator.config import SimModelConfig
from backend.simulator.provider import ProviderSimulator
from tests.fakes import ScriptedProvider, make_settings, models, wait_until

CALLBACK_URL = "http://receiver/callbacks/receive"


@pytest.fixture
async def env(redis15, pg_sessionmaker):
    """Gateway + in-process simulator + callback receiver, all over ASGI (no sockets)."""
    created = []

    async def _make(fail_first_n=0, provider=None, model_specs=None, **settings):
        receiver = create_receiver(fail_first_n=fail_first_n)
        rt = Runtime(
            make_settings(**settings),
            models=model_specs or models(("model-a", 10**6, 10**9), ("model-b", 10**6, 10**9)),
            provider=provider or ScriptedProvider(),
            redis=redis15,
            sessionmaker=pg_sessionmaker,
            callback_http=httpx.AsyncClient(transport=httpx.ASGITransport(app=receiver)),
        )
        await rt.start()
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(runtime=rt)), base_url="http://gateway"
        )
        created.append((rt, client))
        return rt, client, receiver.state.receiver

    yield _make
    for rt, client in created:
        await client.aclose()
        await rt.stop()


def body(model="model-a", tokens=100, **kw):
    return {"model": model, "estimated_tokens": tokens, **kw}


async def batch_done(client, batch_id, timeout=20.0):
    async def done():
        b = (await client.get(f"/v1/batches/{batch_id}")).json()
        finished = b["status"] != "processing"
        return finished and b["callback_status"] in {"delivered", "not_requested", "dead_lettered"}

    await wait_until(done, timeout)
    return (await client.get(f"/v1/batches/{batch_id}")).json()


async def all_results(client, batch_id, page=500):
    items, cursor = [], None
    while True:
        params = {"limit": page, **({"cursor": cursor} if cursor else {})}
        data = (await client.get(f"/v1/batches/{batch_id}/results", params=params)).json()
        items += data["items"]
        cursor = data["next_cursor"]
        if not cursor:
            return items


# ---------- single requests ----------


async def test_single_request_lifecycle(env):
    _, client, _ = await env()
    r = await client.post("/v1/requests", json=body(request_id="my-1", payload={"q": "hi"}))
    assert r.status_code == 202
    ack = r.json()
    assert ack["state"] == "queued" and ack["client_request_id"] == "my-1"

    async def succeeded():
        status = (await client.get(f"/v1/requests/{ack['request_id']}")).json()
        return status["state"] == "succeeded"

    await wait_until(succeeded)


async def test_single_request_rejections(env):
    _, client, _ = await env(model_specs=models(("model-a", 1000, 1000)))
    unknown = await client.post("/v1/requests", json=body(model="nope"))
    assert unknown.status_code == 404 and unknown.json()["state"] == "rejected"
    too_big = await client.post("/v1/requests", json=body(tokens=5000))
    assert too_big.status_code == 422 and "exceeds" in too_big.json()["reason"]
    invalid = await client.post("/v1/requests", json=body(tokens=0))
    assert invalid.status_code == 422  # schema validation: never accepted, never counted
    assert (await client.get("/v1/requests/req_missing")).status_code == 404
    assert (await client.get("/v1/batches/bat_missing")).status_code == 404


async def test_queue_full_returns_429(env):
    _, client, _ = await env(
        model_specs=models(("model-a", 1, 10**6), max_queue=1), limiter_burst_factor=1.0
    )
    codes = [(await client.post("/v1/requests", json=body())).status_code for _ in range(4)]
    assert codes[0] == 202 and 429 in codes


# ---------- batches + callbacks ----------


async def test_batch_scenario3_shape(env):
    """10k-style batch (2,000 here for speed) across 2 models with transient + permanent
    failures; the callback endpoint rejects the first 2 attempts."""
    sim = ProviderSimulator(
        [
            SimModelConfig(
                name=name,
                rpm=10**6,
                tpm=10**9,
                latency_median_ms=0,
                transient_error_rate=0.10,
                permanent_error_rate=0.05,
            )
            for name in ("model-a", "model-b")
        ],
        seed=11,
    )
    rt, client, receiver = await env(fail_first_n=2, provider=InProcessProviderClient(sim))
    requests = [
        body(model="model-a" if i % 2 else "model-b", request_id=f"c-{i}") for i in range(2000)
    ]

    t0 = time.perf_counter()
    r = await client.post("/v1/batches", json={"requests": requests, "callback_url": CALLBACK_URL})
    ack_seconds = time.perf_counter() - t0
    assert r.status_code == 202 and ack_seconds < 1.0
    batch_id = r.json()["batch_id"]

    status = await batch_done(client, batch_id)
    assert status["callback_status"] == "delivered"
    assert status["pending"] == 0
    assert status["succeeded"] + status["failed"] + status["expired"] + status["rejected"] == 2000
    assert status["failed"] > 0  # permanent failures happened

    # callback: delivered on attempt 3, after 2 rejections, and matches the API status
    assert receiver["attempts"][batch_id] == 3
    (cb,) = receiver["received"]
    payload = cb["payload"]
    assert payload["delivery_attempt"] == 3 and cb["idempotency_key"] == batch_id
    for field in ("status", "total", "succeeded", "failed", "expired", "rejected", "results_url"):
        assert payload[field] == status[field]

    # every request id exactly once, all final
    items = await all_results(client, batch_id)
    assert len(items) == 2000
    assert len({i["request_id"] for i in items}) == 2000
    assert {i["client_request_id"] for i in items} == {f"c-{i}" for i in range(2000)}
    assert {i["state"] for i in items} <= {"succeeded", "failed"}

    # attempts logged in Postgres
    async with rt.sessionmaker() as s:
        attempts = (
            await s.scalars(
                select(CallbackAttempt)
                .where(CallbackAttempt.batch_id == batch_id)
                .order_by(CallbackAttempt.attempt_no)
            )
        ).all()
    assert [a.status_code for a in attempts] == [500, 500, 200]

    stats = (await client.get("/v1/stats")).json()["accounting"]
    assert stats["balanced"] and stats["waiting"] == 0


async def test_callback_only_after_every_request_is_final(env):
    rt, client, receiver = await env(provider=ScriptedProvider(latency=0.05))
    reqs = [body(payload={"script": ["transient", "success"]}) for _ in range(50)]
    r = await client.post("/v1/batches", json={"requests": reqs, "callback_url": CALLBACK_URL})
    batch_id = r.json()["batch_id"]
    await batch_done(client, batch_id)
    (cb,) = receiver["received"]
    # every result was already final and readable when the callback arrived
    items = await all_results(client, batch_id)
    assert len(items) == 50
    finished_last = max(i["finished_at"] for i in items)
    assert cb["payload"]["completed_at"] >= finished_last


async def test_batch_without_callback_and_with_rejections(env):
    _, client, _ = await env()
    reqs = [body(), body(model="nope"), body(payload={"script": ["permanent"]})]
    batch_id = (await client.post("/v1/batches", json={"requests": reqs})).json()["batch_id"]
    status = await batch_done(client, batch_id)
    assert status["callback_status"] == "not_requested"
    assert status["status"] == "completed_with_failures"
    assert (status["succeeded"], status["failed"], status["rejected"]) == (1, 1, 1)


async def test_callback_dead_letters_after_max_attempts(env):
    _, client, receiver = await env(fail_first_n=100, callback_max_attempts=3)
    r = await client.post("/v1/batches", json={"requests": [body()], "callback_url": CALLBACK_URL})
    batch_id = r.json()["batch_id"]
    status = await batch_done(client, batch_id)
    assert status["callback_status"] == "dead_lettered"
    assert receiver["attempts"][batch_id] == 3


async def test_batch_validation(env):
    _, client, _ = await env()
    assert (await client.post("/v1/batches", json={"requests": []})).status_code == 422
    dup = {"requests": [body(request_id="x"), body(request_id="x")]}
    assert (await client.post("/v1/batches", json=dup)).status_code == 422
