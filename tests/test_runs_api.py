"""Runs API: listing, details, conflicts and the load-generator command it builds."""

import subprocess
import sys
from datetime import UTC, datetime

import httpx
import pytest

from backend.db.tables import Run, RunMetric, RunResult
from backend.gateway.app import create_app
from backend.gateway.runs_api import RunCreate, _command
from backend.gateway.runtime import Runtime
from tests.fakes import ScriptedProvider, make_settings, models


@pytest.fixture
async def env(redis15, pg_sessionmaker):
    rt = Runtime(
        make_settings(),
        models=models(("model-a", 600, 10**6)),
        provider=ScriptedProvider(),
        redis=redis15,
        sessionmaker=pg_sessionmaker,
        callback_http=httpx.AsyncClient(),
    )
    await rt.start()
    app = create_app(runtime=rt)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")
    yield rt, app, client
    await client.aclose()
    await rt.stop()


async def test_list_and_detail(env):
    rt, _, client = env
    assert (await client.get("/v1/runs")).json() == []
    assert (await client.get("/v1/runs/nope")).status_code == 404
    now = datetime.now(UTC)
    async with rt.sessionmaker() as s, s.begin():
        s.add(
            Run(
                run_id="s1-x",
                scenario="s1",
                status="finished",
                config={},
                summary={"passed": True, "totals": {"completed": 10}},
                started_at=now,
            )
        )
        await s.flush()
        s.add(
            RunMetric(
                run_id="s1-x",
                ts_second=now,
                model="model-a",
                requests=5,
                tokens=50,
                succeeded=5,
                failed=0,
                configured_rpm=600,
                configured_tpm=10**6,
            )
        )
        s.add(
            RunResult(
                run_id="s1-x",
                check_name="accounting",
                passed=True,
                measured="{}",
                expected="balanced",
            )
        )
    listed = (await client.get("/v1/runs")).json()
    assert listed[0]["run_id"] == "s1-x" and listed[0]["passed"] is True
    detail = (await client.get("/v1/runs/s1-x")).json()
    assert detail["checks"][0]["name"] == "accounting"
    assert detail["timeseries"][0]["requests"] == 5


async def test_only_one_run_at_a_time(env):
    _, app, client = env
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    app.state.run_procs = {"s1-busy": sleeper}
    try:
        r = await client.post("/v1/runs", json={"scenario": "s3"})
        assert r.status_code == 409
        listed = (await client.get("/v1/runs")).json()
        assert listed[0] == {
            **listed[0],
            "run_id": "s1-busy",
            "status": "running",
            "stoppable": True,
        }
        assert (await client.post("/v1/runs/s1-busy/stop")).json()["status"] == "stopping"
        sleeper.wait(timeout=5)
        assert (await client.post("/v1/runs/s1-busy/stop")).status_code == 404
    finally:
        sleeper.kill()


async def test_validation(env):
    _, _, client = env
    assert (await client.post("/v1/runs", json={"scenario": "s9"})).status_code == 422


def test_command_for_scenario_and_custom():
    rt = type("RT", (), {"settings": make_settings(simulator_url="http://sim:9000")})()
    cmd = _command(RunCreate(scenario="s2", duration_seconds=30), "s2-1", rt)
    assert cmd[1:5] == ["-m", "loadgen", "scenario", "scenarios/s2.yaml"]
    assert ["--duration", "30.0"] == cmd[5:7] and "--run-id" in cmd
    custom = _command(
        RunCreate(scenario="custom", rate=50, models={"model-a": 1}, callback=True), "c-1", rt
    )
    assert "run" in custom and "--callback" in custom
    assert custom[custom.index("--models") + 1] == "model-a=1.0"
