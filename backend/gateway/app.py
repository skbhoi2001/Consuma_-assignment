"""Gateway API.

Run:  uvicorn backend.gateway.app:create_app --factory --port 8000
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from backend.common.ids import new_batch_id
from backend.common.schemas import (
    BatchAck,
    BatchCreate,
    BatchResultsPage,
    BatchSummary,
    InferenceRequest,
    RequestAck,
    RequestStatus,
)
from backend.common.settings import get_settings
from backend.common.states import BatchStatus, CallbackStatus, RequestState
from backend.db.seed import load_runtime_models
from backend.db.session import get_sessionmaker
from backend.db.tables import BatchRow, CallbackAttempt, RequestRow
from backend.dispatcher.dispatcher import REJECT_QUEUE_FULL, REJECT_UNKNOWN_MODEL
from backend.gateway import metrics_api, models_api, runs_api
from backend.gateway.metrics_api import accounting
from backend.gateway.runtime import Runtime


def _status_from_row(row: RequestRow) -> RequestStatus:
    return RequestStatus(
        request_id=row.request_id,
        client_request_id=row.client_request_id,
        batch_id=row.batch_id,
        model=row.model,
        state=RequestState(row.state),
        attempts=row.attempts,
        estimated_tokens=row.estimated_tokens,
        actual_tokens=row.actual_tokens,
        result=row.result,
        error=row.error,
        submitted_at=row.submitted_at,
        finished_at=row.finished_at,
        latency_ms=row.latency_ms,
    )


def create_app(runtime: Runtime | None = None) -> FastAPI:
    """If `runtime` is given, the caller starts/stops it (tests). Otherwise the app builds and
    manages its own from settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if runtime is not None:
            app.state.rt = runtime
            yield
            return
        sessionmaker = get_sessionmaker()
        settings = get_settings()
        models = await load_runtime_models(sessionmaker, settings.models_config_path)
        rt = Runtime(settings, models=models, sessionmaker=sessionmaker)
        app.state.rt = rt
        await rt.start()
        try:
            yield
        finally:
            await rt.stop()

    app = FastAPI(title="Inference Gateway", version="0.1.0", lifespan=lifespan)
    app.include_router(models_api.router)
    app.include_router(metrics_api.router)
    app.include_router(runs_api.router)
    if runtime is not None:
        app.state.rt = runtime

    def rt(request: Request) -> Runtime:
        return request.app.state.rt

    # ---------- single requests ----------

    @app.post("/v1/requests", status_code=202, response_model=RequestAck)
    async def submit_request(body: InferenceRequest, request: Request) -> JSONResponse:
        r = rt(request)
        job = r.dispatcher.new_job(body)
        rejected = await r.dispatcher.submit([job])
        ack = RequestAck(
            request_id=job.request_id,
            client_request_id=job.client_request_id,
            state=job.state,
            accepted_at=datetime.now(UTC),
            reason=job.error if rejected else None,
        )
        if not rejected:
            code = 202
        elif job.error.startswith(REJECT_UNKNOWN_MODEL):
            code = 404
        elif job.error == REJECT_QUEUE_FULL:
            code = 429  # backpressure: the client should slow down
        else:
            code = 422  # request can never fit the model's limits
        return JSONResponse(ack.model_dump(mode="json"), status_code=code)

    @app.get("/v1/requests/{request_id}", response_model=RequestStatus)
    async def get_request(request_id: str, request: Request) -> RequestStatus:
        r = rt(request)
        status = await r.store.get_request(request_id)
        if status is not None:
            return status
        async with r.sessionmaker() as session:
            row = await session.get(RequestRow, request_id)
        if row is None:
            raise HTTPException(404, "unknown request_id")
        return _status_from_row(row)

    # ---------- batches ----------

    @app.post("/v1/batches", status_code=202, response_model=BatchAck)
    async def submit_batch(body: BatchCreate, request: Request) -> BatchAck:
        r = rt(request)
        batch_id = new_batch_id()
        callback_url = str(body.callback_url) if body.callback_url else None
        jobs = [r.dispatcher.new_job(req, batch_id=batch_id) for req in body.requests]
        created_at = jobs[0].submitted_at
        callback_status = CallbackStatus.PENDING if callback_url else CallbackStatus.NOT_REQUESTED

        # Postgres row first (results reference it), then Redis counters, then enqueue.
        async with r.sessionmaker() as session, session.begin():
            session.add(
                BatchRow(
                    batch_id=batch_id,
                    status=BatchStatus.PROCESSING.value,
                    callback_url=callback_url,
                    callback_status=callback_status.value,
                    total=len(jobs),
                    succeeded=0,
                    failed=0,
                    expired=0,
                    rejected=0,
                    created_at=datetime.fromtimestamp(created_at, UTC),
                )
            )
        await r.store.create_batch(batch_id, len(jobs), callback_url, created_at)
        await r.dispatcher.submit(jobs)
        return BatchAck(
            batch_id=batch_id,
            status=BatchStatus.PROCESSING,
            total=len(jobs),
            accepted_at=datetime.now(UTC),
            status_url=f"{r.settings.public_base_url}/v1/batches/{batch_id}",
        )

    @app.get("/v1/batches")
    async def list_batches(request: Request, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
        """Most recent batches first (Postgres record; live counts via GET /v1/batches/{id})."""
        r = rt(request)
        async with r.sessionmaker() as session:
            rows = (
                await session.scalars(
                    select(BatchRow).order_by(BatchRow.created_at.desc()).limit(limit)
                )
            ).all()
        out = []
        for row in rows:
            live = await r.callbacks.summary(row.batch_id)
            out.append(
                live.model_dump(mode="json")
                if live
                else {
                    "batch_id": row.batch_id,
                    "status": row.status,
                    "total": row.total,
                    "succeeded": row.succeeded,
                    "failed": row.failed,
                    "expired": row.expired,
                    "rejected": row.rejected,
                    "created_at": row.created_at,
                    "completed_at": row.completed_at,
                    "callback_status": row.callback_status,
                }
            )
        return out

    @app.get("/v1/batches/{batch_id}/callbacks")
    async def batch_callbacks(batch_id: str, request: Request) -> list[dict]:
        """Every callback delivery attempt for a batch, oldest first."""
        r = rt(request)
        async with r.sessionmaker() as session:
            rows = (
                await session.scalars(
                    select(CallbackAttempt)
                    .where(CallbackAttempt.batch_id == batch_id)
                    .order_by(CallbackAttempt.attempt_no)
                )
            ).all()
        return [
            {
                "attempt_no": a.attempt_no,
                "status_code": a.status_code,
                "error": a.error,
                "duration_ms": a.duration_ms,
                "sent_at": a.sent_at,
                "next_retry_at": a.next_retry_at,
            }
            for a in rows
        ]

    @app.get("/v1/batches/{batch_id}", response_model=BatchSummary)
    async def get_batch(batch_id: str, request: Request) -> BatchSummary:
        r = rt(request)
        summary = await r.callbacks.summary(batch_id)
        if summary is not None:
            return summary
        async with r.sessionmaker() as session:
            row = await session.get(BatchRow, batch_id)
        if row is None:
            raise HTTPException(404, "unknown batch_id")
        done = row.succeeded + row.failed + row.expired + row.rejected
        return BatchSummary(
            batch_id=row.batch_id,
            status=BatchStatus(row.status),
            total=row.total,
            succeeded=row.succeeded,
            failed=row.failed,
            expired=row.expired,
            rejected=row.rejected,
            pending=max(0, row.total - done),
            created_at=row.created_at,
            completed_at=row.completed_at,
            callback_status=CallbackStatus(row.callback_status),
            results_url=f"{r.settings.public_base_url}/v1/batches/{batch_id}/results",
        )

    @app.get("/v1/batches/{batch_id}/results", response_model=BatchResultsPage)
    async def get_batch_results(
        batch_id: str,
        request: Request,
        cursor: str | None = None,
        limit: int = Query(1000, ge=1, le=5000),
    ) -> BatchResultsPage:
        """Final results, ordered by request_id. Pass next_cursor back as ?cursor= to page."""
        r = rt(request)
        async with r.sessionmaker() as session:
            if await session.get(BatchRow, batch_id) is None:
                raise HTTPException(404, "unknown batch_id")
            stmt = select(RequestRow).where(RequestRow.batch_id == batch_id)
            if cursor:
                stmt = stmt.where(RequestRow.request_id > cursor)
            rows = (await session.scalars(stmt.order_by(RequestRow.request_id).limit(limit))).all()
        items = [_status_from_row(row) for row in rows]
        next_cursor = items[-1].request_id if len(items) == limit else None
        return BatchResultsPage(batch_id=batch_id, items=items, next_cursor=next_cursor)

    # ---------- accounting / health ----------

    @app.get("/v1/stats")
    async def stats(request: Request) -> dict:
        """submitted = completed + waiting + expired + rejected, checked live."""
        r = rt(request)
        return {"accounting": await accounting(r), "models": r.dispatcher.lane_stats()}

    @app.get("/healthz")
    async def healthz(request: Request) -> dict:
        """pid + started_at let a test prove the process was never restarted."""
        r = rt(request)
        return {
            "status": "ok",
            "pid": r.pid,
            "started_at": r.started_at,
            "uptime_seconds": round(time.time() - r.started_at, 3),
        }

    return app
