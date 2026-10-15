"""Dispatcher behaviour: success, retries, failures, expiry, backpressure, limits, accounting."""

import httpx
import pytest
from sqlalchemy import func, select

from backend.common.schemas import InferenceRequest
from backend.common.states import RequestState as S
from backend.db.tables import RequestRow
from backend.dispatcher.provider_client import InProcessProviderClient
from backend.gateway.runtime import Runtime
from backend.simulator.config import SimModelConfig
from backend.simulator.provider import ProviderSimulator
from tests.fakes import ScriptedProvider, make_settings, models, wait_until


@pytest.fixture
async def make_runtime(redis15, pg_sessionmaker):
    runtimes: list[Runtime] = []

    async def _make(model_specs, provider=None, **settings):
        rt = Runtime(
            make_settings(**settings),
            models=model_specs,
            provider=provider or ScriptedProvider(),
            redis=redis15,
            sessionmaker=pg_sessionmaker,
            callback_http=httpx.AsyncClient(),
        )
        await rt.start()
        runtimes.append(rt)
        return rt

    yield _make
    for rt in runtimes:
        await rt.stop()


def req(model="m", tokens=100, **payload):
    return InferenceRequest(model=model, estimated_tokens=tokens, payload=payload)


async def submit(rt, *requests):
    jobs = [rt.dispatcher.new_job(r) for r in requests]
    await rt.dispatcher.submit(jobs)
    return jobs


async def final(rt, job, timeout=5.0):
    async def done():
        status = await rt.store.get_request(job.request_id)
        return status is not None and status.state in {S.SUCCEEDED, S.FAILED, S.EXPIRED}

    await wait_until(done, timeout)
    return await rt.store.get_request(job.request_id)


async def test_success(make_runtime):
    rt = await make_runtime(models(("m", 1000, 10**6)))
    (job,) = await submit(rt, req())
    status = await final(rt, job)
    assert status.state == S.SUCCEEDED and status.attempts == 1 and status.latency_ms >= 0


async def test_transient_errors_are_retried_then_succeed(make_runtime):
    rt = await make_runtime(models(("m", 1000, 10**6)))
    (job,) = await submit(rt, req(script=["transient", "transient", "success"]))
    status = await final(rt, job)
    assert status.state == S.SUCCEEDED and status.attempts == 3


async def test_retries_exhausted_fails(make_runtime):
    rt = await make_runtime(models(("m", 1000, 10**6)), max_attempts=3)
    (job,) = await submit(rt, req(script=["transient"]))
    status = await final(rt, job)
    assert status.state == S.FAILED and status.attempts == 3
    assert "gave up after 3 attempts" in status.error


async def test_permanent_error_fails_without_retry(make_runtime):
    rt = await make_runtime(models(("m", 1000, 10**6)))
    (job,) = await submit(rt, req(script=["permanent"]))
    status = await final(rt, job)
    assert status.state == S.FAILED and status.attempts == 1


async def test_unknown_model_too_big_and_queue_full_are_rejected(make_runtime):
    # rpm=1 + per-second pacing keeps the queue from draining during the test
    rt = await make_runtime(models(("m", 1, 1_000), max_queue=2), limiter_burst_factor=1.0)
    jobs = await submit(rt, req(model="nope"), req(tokens=5_000), req(), req(), req(), req())
    errors = [j.error for j in jobs]
    assert errors[0] == "unknown model 'nope'"
    assert "exceeds" in errors[1]
    assert errors[2:4] == [None, None]  # first 2 fit in the queue (one may already be sent)
    assert errors[4:] == ["queue full", "queue full"]
    assert [j.state for j in jobs if j.error] == [S.REJECTED] * 4


async def test_waiting_too_long_expires(make_runtime):
    # 1 request per minute: the first goes out, the next two can't within max_wait=0.3 s
    rt = await make_runtime(models(("m", 1, 10**6), max_wait=0.3))
    jobs = await submit(rt, req(), req(), req())
    states = [(await final(rt, j)).state for j in jobs]
    assert states == [S.SUCCEEDED, S.EXPIRED, S.EXPIRED]


async def test_limits_respected_end_to_end(make_runtime):
    """Real limiter + simulator referee in observe mode: 0 violations allowed."""
    sim = ProviderSimulator(
        [SimModelConfig(name="m", rpm=30, tpm=10**6, latency_median_ms=0, enforce_limits=False)]
    )
    rt = await make_runtime(
        models(("m", 30, 10**6)), provider=InProcessProviderClient(sim), limiter_burst_factor=None
    )
    await submit(rt, *[req() for _ in range(60)])

    async def thirty_done():
        return sim.stats()["m"]["accepted"] >= 30

    await wait_until(thirty_done, 3)
    stats = sim.stats()["m"]
    assert stats["accepted"] == 30  # the other 30 must wait ~a minute
    assert stats["violations"] == 0 and stats["max_requests_in_any_60s"] <= 30


async def test_accounting_balances_and_results_persisted_once(make_runtime, pg_sessionmaker):
    rt = await make_runtime(models(("m", 10_000, 10**8)))
    scripts = [["success"], ["permanent"], ["transient", "success"], ["transient"]] * 25
    jobs = await submit(rt, *[req(script=s) for s in scripts], req(model="nope"))
    for job in jobs[:-1]:
        await final(rt, job)
    await rt.writer.flush()

    c = await rt.store.counters()
    assert c["submitted"] == 101
    assert c["succeeded"] == 50 and c["failed"] == 50 and c["rejected"] == 1
    assert rt.dispatcher.waiting() == 0
    completed = c["succeeded"] + c["failed"]
    assert c["submitted"] == completed + rt.dispatcher.waiting() + c["expired"] + c["rejected"]

    async with pg_sessionmaker() as s:
        rows = await s.scalar(select(func.count()).select_from(RequestRow))
        distinct = await s.scalar(select(func.count(func.distinct(RequestRow.request_id))))
    assert rows == distinct == 101
