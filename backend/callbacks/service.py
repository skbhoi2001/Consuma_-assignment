"""Batch completion and callback delivery.

1. When a batch's pending counter hits 0, `batch_completed` runs (exactly once per batch):
   it first flushes results to Postgres - so the results_url already works when the client
   hears about it - then marks the batch complete and schedules the callback.
2. `run` delivers due callbacks. Failures are retried with exponential backoff; after
   `callback_max_attempts` the batch is marked dead_lettered. Every attempt is logged in
   `callback_attempts`.

Delivery is at-least-once: each POST carries `Idempotency-Key: <batch_id>` so a receiver can
ignore duplicates.
"""

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.common.schemas import BatchSummary, CallbackPayload
from backend.common.settings import Settings
from backend.common.states import BatchStatus, CallbackStatus
from backend.db import keys
from backend.db.tables import BatchRow, CallbackAttempt
from backend.dispatcher.results_writer import ResultsWriter
from backend.dispatcher.store import RedisStateStore, summary_from_hash

log = logging.getLogger(__name__)


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


class CallbackService:
    def __init__(
        self,
        store: RedisStateStore,
        sessionmaker: async_sessionmaker[AsyncSession],
        writer: ResultsWriter,
        settings: Settings,
        http: httpx.AsyncClient,
        *,
        poll_interval: float = 0.1,
    ) -> None:
        self._store = store
        self._redis = store.redis
        self._sessionmaker = sessionmaker
        self._writer = writer
        self._settings = settings
        self._http = http
        self._poll = poll_interval
        self._task: asyncio.Task[None] | None = None

    async def summary(self, batch_id: str) -> BatchSummary | None:
        found = await self._store.get_batch(batch_id)
        if found is None:
            return None
        h, pending = found
        return summary_from_hash(h, pending, self._settings.public_base_url)

    # ---------- completion ----------

    async def batch_completed(self, batch_id: str) -> None:
        await self._writer.flush()  # results must be readable before anyone is told

        found = await self._store.get_batch(batch_id)
        if found is None:
            return
        h, _ = found
        problems = int(h["failed"]) + int(h["expired"]) + int(h["rejected"])
        status = BatchStatus.COMPLETED_WITH_FAILURES if problems else BatchStatus.COMPLETED
        callback_url = h.get("callback_url") or None
        callback_status = (
            CallbackStatus.DELIVERING if callback_url else CallbackStatus.NOT_REQUESTED
        )
        now = time.time()

        await self._store.update_batch(
            batch_id,
            status=status.value,
            completed_at=now,
            callback_status=callback_status.value,
        )
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                update(BatchRow)
                .where(BatchRow.batch_id == batch_id)
                .values(
                    status=status.value,
                    succeeded=int(h["succeeded"]),
                    failed=int(h["failed"]),
                    expired=int(h["expired"]),
                    rejected=int(h["rejected"]),
                    completed_at=_dt(now),
                    callback_status=callback_status.value,
                )
            )
        if callback_url:
            await self._redis.zadd(keys.CALLBACK_ZSET, {batch_id: now})

    # ---------- delivery ----------

    async def _run(self) -> None:
        while True:
            try:
                due = await self._redis.zrangebyscore(
                    keys.CALLBACK_ZSET, "-inf", time.time(), start=0, num=50
                )
                for batch_id in due:
                    # ZREM is the claim: only the caller that removes it delivers it
                    if await self._redis.zrem(keys.CALLBACK_ZSET, batch_id):
                        await self.deliver(batch_id)
            except Exception:
                log.exception("callback loop error")
            await asyncio.sleep(self._poll)

    async def deliver(self, batch_id: str) -> bool:
        found = await self._store.get_batch(batch_id)
        if found is None:
            return False
        h, pending = found
        url = h["callback_url"]
        summary = summary_from_hash(h, pending, self._settings.public_base_url)
        attempt = await self._store.next_callback_attempt(batch_id)
        payload = CallbackPayload(
            batch_id=batch_id,
            status=summary.status,
            total=summary.total,
            succeeded=summary.succeeded,
            failed=summary.failed,
            expired=summary.expired,
            rejected=summary.rejected,
            results_url=summary.results_url,
            completed_at=summary.completed_at,
            delivery_attempt=attempt,
        )

        started = time.time()
        status_code: int | None = None
        error: str | None = None
        try:
            r = await self._http.post(
                url,
                content=payload.model_dump_json(),
                headers={"content-type": "application/json", "Idempotency-Key": batch_id},
                timeout=self._settings.callback_timeout_seconds,
            )
            status_code = r.status_code
            if not 200 <= r.status_code < 300:
                error = f"HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"
        ok = error is None
        finished = time.time()

        next_retry: float | None = None
        if ok:
            new_status = CallbackStatus.DELIVERED
        elif attempt < self._settings.callback_max_attempts:
            new_status = CallbackStatus.DELIVERING
            base = self._settings.callback_base_delay_ms / 1000
            delay = min(base * 2 ** (attempt - 1), self._settings.callback_max_delay_ms / 1000)
            next_retry = finished + delay
        else:
            new_status = CallbackStatus.DEAD_LETTERED

        async with self._sessionmaker() as session, session.begin():
            session.add(
                CallbackAttempt(
                    batch_id=batch_id,
                    attempt_no=attempt,
                    status_code=status_code,
                    error=error,
                    duration_ms=(finished - started) * 1000,
                    sent_at=_dt(started),
                    next_retry_at=_dt(next_retry) if next_retry else None,
                )
            )
            values: dict = {"callback_status": new_status.value}
            if ok:
                values["callback_delivered_at"] = _dt(finished)
            await session.execute(
                update(BatchRow).where(BatchRow.batch_id == batch_id).values(**values)
            )
        await self._store.update_batch(batch_id, callback_status=new_status.value)
        if next_retry is not None:
            await self._redis.zadd(keys.CALLBACK_ZSET, {batch_id: next_retry})
        if new_status is CallbackStatus.DEAD_LETTERED:
            log.error("callback for batch %s dead-lettered after %d attempts", batch_id, attempt)
        return ok

    # ---------- lifecycle ----------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="callback-delivery")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
