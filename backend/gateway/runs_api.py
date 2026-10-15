"""Start, stop and inspect load-test runs from the API (used by the frontend).

A run is the load generator (`python -m loadgen ...`) started as a separate process, so the
load it creates doesn't share the gateway's event loop. The load generator records the run in
Postgres (runs, run_metrics, run_results); these routes read it back.
"""

import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.db.tables import Run, RunMetric, RunResult
from backend.gateway.runtime import Runtime

router = APIRouter(prefix="/v1/runs", tags=["runs"])
SCENARIOS = ("s1", "s2", "s3")


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Literal["s1", "s2", "s3", "custom"]
    duration_seconds: float | None = Field(None, gt=0, description="override (quick try-outs)")
    # custom runs only
    rate: float = Field(200, gt=0)
    models: dict[str, float] = Field(default_factory=lambda: {"model-a": 0.5, "model-b": 0.5})
    batch_size: int = Field(1, ge=1, le=100_000)
    tokens: int = Field(1000, gt=0)
    callback: bool = False


def _procs(request: Request) -> dict[str, subprocess.Popen]:
    if not hasattr(request.app.state, "run_procs"):
        request.app.state.run_procs = {}
    return request.app.state.run_procs


def _rt(request: Request) -> Runtime:
    return request.app.state.rt


def _command(body: RunCreate, run_id: str, rt: Runtime) -> list[str]:
    common = [
        "--run-id",
        run_id,
        "--gateway",
        rt.settings.public_base_url,
        "--simulator",
        rt.settings.simulator_url,
    ]
    if body.scenario in SCENARIOS:
        cmd = [sys.executable, "-m", "loadgen", "scenario", f"scenarios/{body.scenario}.yaml"]
        if body.duration_seconds:
            cmd += ["--duration", str(body.duration_seconds)]
        return cmd + common
    models = ",".join(f"{m}={w}" for m, w in body.models.items())
    cmd = [
        sys.executable,
        "-m",
        "loadgen",
        "run",
        "--rate",
        str(body.rate),
        "--duration",
        str(body.duration_seconds or 60),
        "--models",
        models,
        "--batch-size",
        str(body.batch_size),
        "--tokens",
        str(body.tokens),
    ]
    if body.callback:
        cmd.append("--callback")
    return cmd + common


@router.post("", status_code=202)
async def start_run(body: RunCreate, request: Request) -> dict:
    procs = _procs(request)
    running = [rid for rid, p in procs.items() if p.poll() is None]
    if running:
        raise HTTPException(409, f"run {running[0]} is still in progress")
    run_id = f"{body.scenario}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    Path("logs/runs").mkdir(parents=True, exist_ok=True)
    log = open(f"logs/runs/{run_id}.log", "w")  # noqa: SIM115 - owned by the child process
    procs[run_id] = subprocess.Popen(
        _command(body, run_id, _rt(request)), stdout=log, stderr=subprocess.STDOUT
    )
    return {"run_id": run_id, "status": "starting", "pid": procs[run_id].pid}


@router.post("/{run_id}/stop")
async def stop_run(run_id: str, request: Request) -> dict:
    proc = _procs(request).get(run_id)
    if proc is None or proc.poll() is not None:
        raise HTTPException(404, "no running process for this run")
    proc.send_signal(signal.SIGTERM)  # the load generator stops, drains briefly, reports
    return {"run_id": run_id, "status": "stopping"}


def _status(row: Run | None, proc: subprocess.Popen | None) -> str:
    if proc is not None and proc.poll() is None:
        return "running"
    if row is None:
        return "failed" if proc is not None else "unknown"
    if row.status == "running" and proc is not None:
        return "failed"  # process exited without writing its report
    return row.status


@router.get("")
async def list_runs(request: Request, limit: int = 50) -> list[dict]:
    rt, procs = _rt(request), _procs(request)
    async with rt.sessionmaker() as session:
        rows = (
            await session.scalars(select(Run).order_by(Run.started_at.desc()).limit(limit))
        ).all()
    known = {r.run_id for r in rows}
    out = [
        {
            "run_id": rid,
            "scenario": rid.split("-")[0],
            "status": _status(None, p),
            "stoppable": p.poll() is None,
            "started_at": None,
            "ended_at": None,
            "passed": None,
        }
        for rid, p in procs.items()
        if rid not in known
    ]
    for r in rows:
        summary = r.summary or {}
        out.append(
            {
                "run_id": r.run_id,
                "scenario": r.scenario,
                "status": _status(r, procs.get(r.run_id)),
                # only runs this gateway started can be stopped from the API
                "stoppable": r.run_id in procs and procs[r.run_id].poll() is None,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
                "passed": summary.get("passed"),
                "totals": summary.get("totals"),
            }
        )
    return out


@router.get("/{run_id}")
async def get_run(run_id: str, request: Request) -> dict:
    rt = _rt(request)
    async with rt.sessionmaker() as session:
        row = await session.get(Run, run_id)
        if row is None:
            proc = _procs(request).get(run_id)
            if proc is None:
                raise HTTPException(404, "unknown run")
            return {"run_id": run_id, "status": _status(None, proc)}
        metrics = (
            await session.scalars(
                select(RunMetric).where(RunMetric.run_id == run_id).order_by(RunMetric.ts_second)
            )
        ).all()
        results = (await session.scalars(select(RunResult).where(RunResult.run_id == run_id))).all()
    return {
        "run_id": row.run_id,
        "scenario": row.scenario,
        "status": _status(row, _procs(request).get(run_id)),
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "config": row.config,
        "machine_info": row.machine_info,
        "summary": row.summary,
        "report_dir": row.report_dir,
        "checks": [
            {
                "name": c.check_name,
                "passed": c.passed,
                "measured": c.measured,
                "expected": c.expected,
            }
            for c in results
        ],
        "timeseries": [
            {
                "ts": m.ts_second.timestamp(),
                "model": m.model,
                "requests": m.requests,
                "tokens": m.tokens,
                "succeeded": m.succeeded,
                "failed": m.failed,
                "configured_rpm": m.configured_rpm,
                "configured_tpm": m.configured_tpm,
            }
            for m in metrics
        ],
    }
