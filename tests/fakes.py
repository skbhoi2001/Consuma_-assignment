"""Test doubles and helpers for the dispatcher / gateway tests."""

import asyncio
import time
from collections.abc import Awaitable, Callable

from backend.common.schemas import ModelConfig
from backend.common.settings import Settings
from backend.dispatcher.job import Job
from backend.dispatcher.provider_client import ProviderResult


class ScriptedProvider:
    """Outcome per attempt comes from the request payload, e.g.
    {"script": ["transient", "transient", "success"]}. Default: success."""

    def __init__(self, latency: float = 0.0) -> None:
        self.latency = latency
        self.calls: list[tuple[str, int, float]] = []  # (request_id, attempt, time)

    async def call(self, job: Job) -> ProviderResult:
        self.calls.append((job.request_id, job.attempts, time.time()))
        if self.latency:
            await asyncio.sleep(self.latency)
        script = job.payload.get("script", ["success"])
        outcome = script[min(job.attempts - 1, len(script) - 1)]
        if outcome == "success":
            return ProviderResult("success", 200, actual_tokens=job.estimated_tokens, output={})
        code = 503 if outcome == "transient" else 400
        return ProviderResult(outcome, code, error=f"HTTP {code}: scripted {outcome}")

    async def aclose(self) -> None:
        return None


def make_settings(**overrides) -> Settings:
    base = dict(
        max_attempts=3,
        retry_base_delay_ms=10,
        retry_max_delay_ms=50,
        callback_base_delay_ms=20,
        callback_max_delay_ms=100,
        callback_max_attempts=5,
        results_flush_interval_ms=20,
        target_utilization=1.0,
        limiter_burst_factor=None,
        public_base_url="http://gateway",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def models(*specs: tuple[str, int, int], max_queue: int = 100_000, max_wait: float = 60):
    return {
        name: ModelConfig(
            name=name, rpm=rpm, tpm=tpm, max_queue=max_queue, max_wait_seconds=max_wait
        )
        for name, rpm, tpm in specs
    }


async def wait_until(
    cond: Callable[[], Awaitable[bool]], timeout: float = 10.0, interval: float = 0.02
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await cond():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met in time")
