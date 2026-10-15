"""The dispatcher: per-model queues, a scheduler per model, and provider calls.

Flow for one request (see docs/workflow.pdf, page 2):

    submit -> QUEUED -> (deadline passed?) -> EXPIRED
                     -> (limiter allows?)  -> DISPATCHED -> provider call
                                                 success   -> SUCCEEDED
                                                 transient -> RETRY_WAIT -> backoff -> QUEUED
                                                 permanent / out of retries -> FAILED

Every model has its own lane (queue + limiter + scheduler task), so a throttled model never
blocks another one. Demand above capacity waits in the bounded queue (overflow -> REJECTED)
and gives up after max_wait (-> EXPIRED). Nothing is dropped silently.
"""

import asyncio
import contextlib
import logging
import math
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from backend.common.ids import new_request_id
from backend.common.schemas import InferenceRequest, ModelConfig
from backend.common.settings import Settings
from backend.common.states import RequestState as S
from backend.dispatcher.job import Job
from backend.dispatcher.limiter import LimiterRegistry
from backend.dispatcher.provider_client import ProviderClient, ProviderResult
from backend.dispatcher.results_writer import ResultsWriter
from backend.dispatcher.store import RedisStateStore
from backend.metrics.collector import MetricsCollector

log = logging.getLogger(__name__)

BatchCompleteHook = Callable[[str], Awaitable[None]]

# Rejection reasons (job.error) - the API maps each to an HTTP status code
REJECT_UNKNOWN_MODEL = "unknown model"
REJECT_QUEUE_FULL = "queue full"
REJECT_TOO_BIG = "estimated_tokens exceeds the model's TPM limit"


class Lane:
    """Everything belonging to one model."""

    def __init__(self, cfg: ModelConfig) -> None:
        self.cfg = cfg
        self.queue: deque[Job] = deque()
        self.wake = asyncio.Event()
        self.inflight = 0
        self.retry_waiting = 0
        self.task: asyncio.Task[None] | None = None

    @property
    def waiting(self) -> int:
        return len(self.queue) + self.inflight + self.retry_waiting


class Dispatcher:
    def __init__(
        self,
        models: dict[str, ModelConfig],
        registry: LimiterRegistry,
        provider: ProviderClient,
        store: RedisStateStore,
        settings: Settings,
        *,
        writer: ResultsWriter | None = None,
        metrics: MetricsCollector | None = None,
        on_batch_complete: BatchCompleteHook | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._lanes = {name: Lane(cfg) for name, cfg in models.items()}
        self._registry = registry
        self._provider = provider
        self._store = store
        self._writer = writer
        self._metrics = metrics
        self._on_batch_complete = on_batch_complete
        self._clock = clock
        self._max_attempts = settings.max_attempts
        self._max_inflight = settings.max_inflight_per_model
        self._retry_base = settings.retry_base_delay_ms / 1000
        self._retry_max = settings.retry_max_delay_ms / 1000
        self._tasks: set[asyncio.Task[Any]] = set()
        self._rng = random.Random()

    # ---------- lifecycle ----------

    async def start(self) -> None:
        for name, lane in self._lanes.items():
            lane.task = asyncio.create_task(self._schedule(lane), name=f"scheduler:{name}")

    async def stop(self) -> None:
        tasks = [lane.task for lane in self._lanes.values() if lane.task] + list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("dispatcher task failed", exc_info=task.exception())

    # ---------- intake ----------

    def new_job(self, req: InferenceRequest, *, batch_id: str | None = None) -> Job:
        now = self._clock()
        lane = self._lanes.get(req.model)
        max_wait = lane.cfg.max_wait_seconds if lane else 0.0
        return Job(
            request_id=new_request_id(),
            client_request_id=req.request_id,
            batch_id=batch_id,
            model=req.model,
            payload=req.payload,
            estimated_tokens=req.estimated_tokens,
            submitted_at=now,
            deadline=now + max_wait,
        )

    async def submit(self, jobs: list[Job]) -> list[Job]:
        """Record and enqueue jobs. Returns the ones that were rejected (with job.error set)."""
        accepted: list[Job] = []
        rejected: list[Job] = []
        added: dict[str, int] = {}

        for job in jobs:
            lane = self._lanes.get(job.model)
            if lane is None:
                job.error = f"{REJECT_UNKNOWN_MODEL} '{job.model}'"
            elif len(lane.queue) + added.get(job.model, 0) >= lane.cfg.max_queue:
                job.error = REJECT_QUEUE_FULL
            elif not self._registry.get(job.model).fits_ever(job.estimated_tokens):
                job.error = REJECT_TOO_BIG
            if job.error:
                rejected.append(job)
            else:
                job.move(S.QUEUED)
                added[job.model] = added.get(job.model, 0) + 1
                accepted.append(job)

        # Record first, then enqueue: a scheduler can't touch a job before it's saved.
        await self._store.save_new(jobs)
        for job in accepted:
            lane = self._lanes[job.model]
            lane.queue.append(job)
            lane.wake.set()

        if rejected:
            now = self._clock()
            for job in rejected:
                job.move(S.REJECTED)
                job.finished_at = now
            await self._finalize(rejected)
        return rejected

    # ---------- scheduling ----------

    async def _schedule(self, lane: Lane) -> None:
        limiter = self._registry.get(lane.cfg.name)
        while True:
            lane.wake.clear()
            if not lane.queue:
                await lane.wake.wait()
                continue

            now = self._clock()
            job = lane.queue[0]
            if now >= job.deadline:
                lane.queue.popleft()
                await self._finish(job, S.EXPIRED, "waited longer than max_wait_seconds")
                continue

            if lane.inflight >= self._max_inflight:
                await lane.wake.wait()  # a finishing call wakes us
                continue

            decision = limiter.try_acquire(job.estimated_tokens, now=now)
            if not decision.allowed:
                if math.isinf(decision.retry_after):  # limits were lowered below this request
                    lane.queue.popleft()
                    await self._finish(
                        job, S.EXPIRED, "estimated_tokens exceeds the model's current TPM limit"
                    )
                    continue
                timeout = max(0.001, min(decision.retry_after, job.deadline - now))
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(lane.wake.wait(), timeout=timeout)
                continue

            lane.queue.popleft()
            job.move(S.DISPATCHED)
            job.attempts += 1
            lane.inflight += 1
            if self._metrics is not None:
                self._metrics.on_dispatch(job.model, job.estimated_tokens, now)
            self._spawn(self._call(lane, job))

    async def _call(self, lane: Lane, job: Job) -> None:
        try:
            await self._store.save_state(job)
            started = time.perf_counter()
            result = await self._provider.call(job)
            job.call_ms = (time.perf_counter() - started) * 1000
        except Exception as exc:  # a bug or outage in the client counts as transient
            result = ProviderResult("transient", error=f"client error: {exc!r}")
        finally:
            lane.inflight -= 1
            lane.wake.set()

        if result.outcome == "success":
            job.actual_tokens = result.actual_tokens
            job.result = result.output
            job.error = None
            await self._finish(job, S.SUCCEEDED)
        elif result.outcome == "transient" and job.attempts < self._max_attempts:
            job.move(S.RETRY_WAIT)
            job.error = result.error
            lane.retry_waiting += 1
            if self._metrics is not None:
                self._metrics.on_retry(job.model, provider_429=result.status_code == 429)
            await self._store.save_state(job)
            self._spawn(self._retry_later(lane, job, self._backoff(job.attempts, result)))
        else:
            error = result.error
            if result.outcome == "transient":
                error = f"{error} (gave up after {job.attempts} attempts)"
            await self._finish(job, S.FAILED, error)

    def _backoff(self, attempt: int, result: ProviderResult) -> float:
        """Exponential backoff with jitter; never sooner than the provider's Retry-After."""
        delay = min(self._retry_base * 2 ** (attempt - 1), self._retry_max)
        delay *= self._rng.uniform(0.5, 1.5)
        return max(delay, result.retry_after or 0.0)

    async def _retry_later(self, lane: Lane, job: Job, delay: float) -> None:
        await asyncio.sleep(delay)
        if self._clock() >= job.deadline:
            lane.retry_waiting -= 1
            await self._finish(job, S.EXPIRED, f"waited longer than max_wait ({job.error})")
            return
        job.move(S.QUEUED)
        await self._store.save_state(job)
        lane.retry_waiting -= 1
        lane.queue.appendleft(job)  # retries go first: they've already waited
        lane.wake.set()

    # ---------- finishing ----------

    async def _finish(self, job: Job, state: S, error: str | None = None) -> None:
        job.move(state)
        job.finished_at = self._clock()
        if error is not None:
            job.error = error
        await self._finalize([job])

    async def _finalize(self, jobs: list[Job]) -> None:
        remaining = await self._store.finalize_many(jobs)
        for job in jobs:
            if self._writer is not None:
                self._writer.add(job.to_row())
            if self._metrics is not None:
                self._metrics.on_final(
                    job.model, job.state, job.latency_ms, job.finished_at, call_ms=job.call_ms
                )
        if self._on_batch_complete is not None:
            for batch_id, left in remaining.items():
                if left == 0:
                    self._spawn(self._on_batch_complete(batch_id))

    # ---------- runtime control / introspection ----------

    def update_limits(self, model: str, rpm: int | None = None, tpm: int | None = None) -> None:
        """Change a model's limits while running. Takes effect on the scheduler's next check."""
        lane = self._lanes[model]  # KeyError if unknown
        self._registry.update_limits(model, rpm=rpm, tpm=tpm)
        limiter = self._registry.get(model)
        lane.cfg = lane.cfg.model_copy(update={"rpm": limiter.rpm, "tpm": limiter.tpm})
        if self._metrics is not None:
            self._metrics.on_limits(model, limiter.rpm, limiter.tpm)
        lane.wake.set()

    @property
    def models(self) -> dict[str, ModelConfig]:
        return {name: lane.cfg for name, lane in self._lanes.items()}

    def waiting(self) -> int:
        return sum(lane.waiting for lane in self._lanes.values())

    def lane_stats(self) -> dict[str, dict[str, int]]:
        limits = self._registry.snapshot()
        return {
            name: {
                "queued": len(lane.queue),
                "inflight": lane.inflight,
                "retry_wait": lane.retry_waiting,
                **limits[name],
            }
            for name, lane in self._lanes.items()
        }
