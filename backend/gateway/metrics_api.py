"""Metrics routes: live snapshot, SSE stream, per-second time series, latency, Prometheus."""

import asyncio
import json
import time
from typing import Literal

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sse_starlette.sse import EventSourceResponse

from backend.gateway.runtime import Runtime

router = APIRouter(tags=["metrics"])


def _rt(request: Request) -> Runtime:
    return request.app.state.rt


async def accounting(rt: Runtime) -> dict:
    """submitted = completed + waiting + expired + rejected, checked live."""
    c = await rt.store.counters()
    waiting = rt.dispatcher.waiting()
    completed = c["succeeded"] + c["failed"]
    return {
        **c,
        "completed": completed,
        "waiting": waiting,
        "balanced": c["submitted"] == completed + waiting + c["expired"] + c["rejected"],
    }


async def live_snapshot(rt: Runtime) -> dict:
    lanes = rt.dispatcher.lane_stats()
    models = {
        name: {**metrics, **{k: lanes[name][k] for k in ("queued", "inflight", "retry_wait")}}
        for name, metrics in rt.metrics.live().items()
        if name in lanes
    }
    return {"ts": time.time(), "accounting": await accounting(rt), "models": models}


@router.get("/v1/metrics/live")
async def live(request: Request) -> dict:
    return await live_snapshot(_rt(request))


@router.get("/v1/stream")
async def stream(request: Request, interval: float = 1.0, max_events: int | None = None):
    """Server-Sent Events: one live snapshot per `interval` seconds (dashboard)."""
    rt = _rt(request)

    async def events():
        sent = 0
        while max_events is None or sent < max_events:
            if await request.is_disconnected():
                break
            yield {"event": "snapshot", "data": json.dumps(await live_snapshot(rt))}
            sent += 1
            if max_events is None or sent < max_events:
                await asyncio.sleep(interval)

    return EventSourceResponse(events())


@router.get("/v1/metrics/timeseries")
async def timeseries(
    request: Request,
    since: float | None = None,
    until: float | None = None,
    model: str | None = None,
) -> dict:
    rt = _rt(request)
    return {
        "rows": rt.metrics.timeseries(since=since, until=until, model=model),
        "limit_changes": [c for c in rt.metrics.limit_changes if since is None or c["ts"] >= since],
    }


@router.get("/v1/metrics/latency")
async def latency(
    request: Request,
    since: float | None = None,
    until: float | None = None,
    model: str | None = None,
    kind: Literal["end_to_end", "provider_call"] = "end_to_end",
) -> dict:
    return _rt(request).metrics.latency(since=since, until=until, model=model, kind=kind)


@router.get("/metrics")
async def prometheus(request: Request) -> Response:
    return Response(generate_latest(_rt(request).metrics.registry), media_type=CONTENT_TYPE_LATEST)
