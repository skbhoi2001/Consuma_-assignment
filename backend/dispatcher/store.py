"""Live request/batch state in Redis.

Redis holds the hot, fast-changing state: one hash per request, counters per batch, and
global accounting counters. Final results are also written to Postgres by ResultsWriter.
"""

from datetime import UTC, datetime
from typing import Any

import orjson
from redis.asyncio import Redis

from backend.common.schemas import BatchSummary, RequestStatus
from backend.common.states import BatchStatus, CallbackStatus, RequestState
from backend.db import keys
from backend.dispatcher.job import Job

# Which batch/global counter each terminal state increments
TERMINAL_FIELD = {
    RequestState.SUCCEEDED: "succeeded",
    RequestState.FAILED: "failed",
    RequestState.EXPIRED: "expired",
    RequestState.REJECTED: "rejected",
}


def _s(value: Any) -> str:
    """Redis stores strings; None becomes ''."""
    return "" if value is None else str(value)


def _opt(value: str | None) -> str | None:
    return value or None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromtimestamp(float(value), UTC) if value else None


def status_from_hash(h: dict[str, str]) -> RequestStatus:
    return RequestStatus(
        request_id=h["request_id"],
        client_request_id=_opt(h.get("client_request_id")),
        batch_id=_opt(h.get("batch_id")),
        model=h["model"],
        state=RequestState(h["state"]),
        attempts=int(h.get("attempts") or 0),
        estimated_tokens=int(h["estimated_tokens"]),
        actual_tokens=int(h["actual_tokens"]) if h.get("actual_tokens") else None,
        result=orjson.loads(h["result"]) if h.get("result") else None,
        error=_opt(h.get("error")),
        submitted_at=_dt(h["submitted_at"]),
        finished_at=_dt(h.get("finished_at")),
        latency_ms=float(h["latency_ms"]) if h.get("latency_ms") else None,
    )


def summary_from_hash(h: dict[str, str], pending: int, base_url: str) -> BatchSummary:
    batch_id = h["batch_id"]
    return BatchSummary(
        batch_id=batch_id,
        status=BatchStatus(h["status"]),
        total=int(h["total"]),
        succeeded=int(h["succeeded"]),
        failed=int(h["failed"]),
        expired=int(h["expired"]),
        rejected=int(h["rejected"]),
        pending=max(0, pending),
        created_at=_dt(h["created_at"]),
        completed_at=_dt(h.get("completed_at")),
        callback_status=CallbackStatus(h["callback_status"]),
        results_url=f"{base_url}/v1/batches/{batch_id}/results",
    )


class RedisStateStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 86_400):
        self.redis = redis
        self.ttl = ttl_seconds

    # ---------- batches ----------

    async def create_batch(
        self, batch_id: str, total: int, callback_url: str | None, created_at: float
    ) -> None:
        callback_status = CallbackStatus.PENDING if callback_url else CallbackStatus.NOT_REQUESTED
        pipe = self.redis.pipeline(transaction=True)
        pipe.hset(
            keys.batch(batch_id),
            mapping={
                "batch_id": batch_id,
                "status": BatchStatus.PROCESSING.value,
                "total": total,
                "succeeded": 0,
                "failed": 0,
                "expired": 0,
                "rejected": 0,
                "created_at": created_at,
                "completed_at": "",
                "callback_url": _s(callback_url),
                "callback_status": callback_status.value,
                "callback_attempts": 0,
            },
        )
        pipe.set(keys.batch_pending(batch_id), total)
        pipe.expire(keys.batch(batch_id), self.ttl)
        pipe.expire(keys.batch_pending(batch_id), self.ttl)
        await pipe.execute()

    async def get_batch(self, batch_id: str) -> tuple[dict[str, str], int] | None:
        pipe = self.redis.pipeline(transaction=False)
        pipe.hgetall(keys.batch(batch_id))
        pipe.get(keys.batch_pending(batch_id))
        h, pending = await pipe.execute()
        if not h:
            return None
        return h, int(pending or 0)

    async def update_batch(self, batch_id: str, **fields: Any) -> None:
        await self.redis.hset(keys.batch(batch_id), mapping={k: _s(v) for k, v in fields.items()})

    async def next_callback_attempt(self, batch_id: str) -> int:
        return int(await self.redis.hincrby(keys.batch(batch_id), "callback_attempts", 1))

    # ---------- requests ----------

    async def save_new(self, jobs: list[Job]) -> None:
        """Record newly received requests (one round trip for the whole list)."""
        pipe = self.redis.pipeline(transaction=False)
        for job in jobs:
            key = keys.request(job.request_id)
            pipe.hset(
                key,
                mapping={
                    "request_id": job.request_id,
                    "client_request_id": _s(job.client_request_id),
                    "batch_id": _s(job.batch_id),
                    "model": job.model,
                    "state": job.state.value,
                    "attempts": job.attempts,
                    "estimated_tokens": job.estimated_tokens,
                    "submitted_at": job.submitted_at,
                },
            )
            pipe.expire(key, self.ttl)
        pipe.hincrby(keys.STATS, "submitted", len(jobs))
        await pipe.execute()

    async def save_state(self, job: Job) -> None:
        """Record a non-final state change (queued / dispatched / retry_wait)."""
        await self.redis.hset(
            keys.request(job.request_id),
            mapping={"state": job.state.value, "attempts": job.attempts, "error": _s(job.error)},
        )

    async def finalize_many(self, jobs: list[Job]) -> dict[str, int]:
        """Record final states atomically with their counters.

        Returns {batch_id: pending_after} for every batch touched. Each request is finalized
        exactly once (the state machine forbids leaving a final state), so the pending counter
        reaches 0 exactly once per batch - that's what triggers the callback.
        """
        pipe = self.redis.pipeline(transaction=True)
        decr_index: list[tuple[int, str]] = []
        n = 0
        for job in jobs:
            field = TERMINAL_FIELD[job.state]
            pipe.hset(
                keys.request(job.request_id),
                mapping={
                    "state": job.state.value,
                    "attempts": job.attempts,
                    "actual_tokens": _s(job.actual_tokens),
                    "result": orjson.dumps(job.result).decode() if job.result is not None else "",
                    "error": _s(job.error),
                    "finished_at": _s(job.finished_at),
                    "latency_ms": _s(job.latency_ms),
                },
            )
            pipe.hincrby(keys.STATS, field, 1)
            n += 2
            if job.batch_id:
                pipe.hincrby(keys.batch(job.batch_id), field, 1)
                pipe.decr(keys.batch_pending(job.batch_id))
                decr_index.append((n + 1, job.batch_id))
                n += 2
        results = await pipe.execute()

        remaining: dict[str, int] = {}
        for idx, batch_id in decr_index:
            remaining[batch_id] = min(remaining.get(batch_id, results[idx]), results[idx])
        return remaining

    async def get_request(self, request_id: str) -> RequestStatus | None:
        h = await self.redis.hgetall(keys.request(request_id))
        return status_from_hash(h) if h else None

    # ---------- accounting ----------

    async def counters(self) -> dict[str, int]:
        raw = await self.redis.hgetall(keys.STATS)
        names = ("submitted", "succeeded", "failed", "expired", "rejected")
        return {name: int(raw.get(name, 0)) for name in names}
