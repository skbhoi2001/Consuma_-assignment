"""Clients that send one request to the (simulated) provider.

The dispatcher only sees ProviderResult: success, transient (retry) or permanent (give up).
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import aiohttp

from backend.dispatcher.job import Job
from backend.simulator.provider import ProviderSimulator

Outcome = Literal["success", "transient", "permanent"]


@dataclass(frozen=True, slots=True)
class ProviderResult:
    outcome: Outcome
    status_code: int | None = None
    actual_tokens: int | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    retry_after: float | None = None  # provider asked us to wait this long (429)


def classify(status_code: int) -> Outcome:
    if status_code == 200:
        return "success"
    if status_code == 429 or status_code >= 500:
        return "transient"
    return "permanent"


class ProviderClient(Protocol):
    async def call(self, job: Job) -> ProviderResult: ...

    async def aclose(self) -> None: ...


class HttpProviderClient:
    """Calls the simulator over HTTP (POST /sim/inference) using aiohttp.

    aiohttp rather than httpx on this hot path: measured against the simulator at a target of
    1,000 req/s, httpx managed ~70 req/s at 76% CPU while aiohttp kept up at ~19% CPU.
    The connection pool is bounded; extra calls wait for a free connection.
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0, max_connections: int = 512):
        self._url = base_url.rstrip("/") + "/sim/inference"
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._max_connections = max_connections
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        # created lazily: an aiohttp session must be created inside the running event loop
        if self._session is None:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=self._max_connections),
                timeout=self._timeout,
            )
        return self._session

    async def call(self, job: Job) -> ProviderResult:
        body = {
            "request_id": job.request_id,
            "model": job.model,
            "estimated_tokens": job.estimated_tokens,
            "payload": job.payload,
        }
        try:
            async with self._get_session().post(self._url, json=body) as r:
                status = r.status
                try:
                    data = await r.json(content_type=None)
                except ValueError:
                    data = {}
        except TimeoutError:
            return ProviderResult("transient", error="provider timeout")
        except aiohttp.ClientError as exc:
            return ProviderResult("transient", error=f"connection error: {exc!r}")

        data = data if isinstance(data, dict) else {}
        outcome = classify(status)
        if outcome == "success":
            return ProviderResult(
                "success", 200, actual_tokens=data.get("actual_tokens"), output=data.get("output")
            )
        return ProviderResult(
            outcome,
            status,
            error=f"HTTP {status}: {data.get('error', 'error')}",
            retry_after=data.get("retry_after"),
        )

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()


class InProcessProviderClient:
    """Calls a ProviderSimulator directly, no network (tests and high-throughput benchmarks)."""

    def __init__(self, simulator: ProviderSimulator):
        self.simulator = simulator

    async def call(self, job: Job) -> ProviderResult:
        out = self.simulator.handle(job.model, job.estimated_tokens)
        if out.latency_ms > 0:
            await asyncio.sleep(out.latency_ms / 1000)
        outcome = classify(out.status_code)
        if outcome == "success":
            return ProviderResult(
                "success",
                200,
                actual_tokens=out.actual_tokens,
                output={"text": f"simulated completion for {job.request_id}"},
            )
        return ProviderResult(
            outcome,
            out.status_code,
            error=f"HTTP {out.status_code}: {out.kind}",
            retry_after=out.retry_after,
        )

    async def aclose(self) -> None:
        return None
