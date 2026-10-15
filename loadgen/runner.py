"""Runs one load test against running services and collects everything the report needs.

Open loop: requests are sent on a fixed schedule (rate x elapsed time) whether or not the
gateway keeps up - that's how we push demand above capacity. Steps:

    1. snapshot gateway counters + process id, apply limits / simulator settings, reset referee
    2. submit for `duration_seconds` (+ scheduled limit changes)
    3. drain: wait until nothing is waiting (and every callback is final)
    4. collect: counter deltas, per-second time series, latency, referee stats, batch timings
"""

import asyncio
import contextlib
import math
import random
import time
from typing import Any

import aiohttp

from loadgen.callback_server import CallbackReceiver
from loadgen.checks import steady_window
from loadgen.config import LoadConfig

FINAL_CALLBACK = {"delivered", "dead_lettered", "not_requested"}


class LoadRunner:
    def __init__(self, cfg: LoadConfig, stop: asyncio.Event | None = None) -> None:
        self.cfg = cfg
        self.stop = stop or asyncio.Event()
        self.rng = random.Random(42)
        self.models = list(cfg.models)
        self.weights = list(cfg.models.values())
        self.sem = asyncio.Semaphore(cfg.max_concurrent_posts)
        self.tasks: set[asyncio.Task] = set()
        # client-side records
        self.sent_requests = 0
        self.http_status: dict[str, int] = {}
        self.http_errors = 0
        self.http_error_requests = 0
        self.ack_ms: list[float] = []
        self.batches: dict[str, dict[str, Any]] = {}  # batch_id -> {submitted_at, size}
        self.limit_change_log: list[dict] = []
        self.aborted = False

    # ---------- helpers ----------

    async def _json(self, s: aiohttp.ClientSession, method: str, url: str, **kw) -> Any:
        async with s.request(method, url, **kw) as r:
            r.raise_for_status()
            return await r.json()

    def _make_request(self) -> dict:
        tokens = self.cfg.tokens
        if self.cfg.token_jitter:
            tokens = max(
                1,
                round(
                    tokens * self.rng.uniform(1 - self.cfg.token_jitter, 1 + self.cfg.token_jitter)
                ),
            )
        model = self.rng.choices(self.models, self.weights)[0]
        return {"model": model, "estimated_tokens": tokens}

    # ---------- submission ----------

    async def _post(self, s: aiohttp.ClientSession, requests: list[dict], callback_url: str | None):
        async with self.sem:
            t0 = time.perf_counter()
            submitted_at = time.time()
            try:
                if self.cfg.batch_size == 1:
                    url = f"{self.cfg.gateway_url}/v1/requests"
                    async with s.post(url, json=requests[0]) as r:
                        status = r.status
                        await r.read()
                    batch_id = None
                else:
                    body: dict[str, Any] = {"requests": requests}
                    if callback_url:
                        body["callback_url"] = callback_url
                    async with s.post(f"{self.cfg.gateway_url}/v1/batches", json=body) as r:
                        status = r.status
                        data = await r.json(content_type=None)
                    batch_id = data.get("batch_id") if isinstance(data, dict) else None
            except (aiohttp.ClientError, TimeoutError):
                self.http_errors += 1
                self.http_error_requests += len(requests)
                return
            self.ack_ms.append((time.perf_counter() - t0) * 1000)
            self.http_status[str(status)] = self.http_status.get(str(status), 0) + 1
            if batch_id:
                self.batches[batch_id] = {"submitted_at": submitted_at, "size": len(requests)}

    async def _submit(self, s: aiohttp.ClientSession, callback_url: str | None, t0: float):
        target_total = math.ceil(self.cfg.rate * self.cfg.duration_seconds)
        generated = 0
        while generated < target_total and not self.stop.is_set():
            elapsed = time.time() - t0
            due = min(target_total, math.floor(elapsed * self.cfg.rate) + 1)
            # send full batches; the final partial batch goes out at the end
            while due - generated >= self.cfg.batch_size or (due == target_total > generated):
                n = min(self.cfg.batch_size, target_total - generated)
                reqs = [self._make_request() for _ in range(n)]
                generated += n
                self.sent_requests += n
                task = asyncio.create_task(self._post(s, reqs, callback_url))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
            if elapsed >= self.cfg.duration_seconds:
                break
            await asyncio.sleep(0.005)
        if self.tasks:
            await asyncio.gather(*list(self.tasks))

    async def _limit_changes(self, s: aiohttp.ClientSession, t0: float) -> None:
        for change in sorted(self.cfg.limit_changes, key=lambda c: c.at_seconds):
            delay = t0 + change.at_seconds - time.time()
            if delay > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.stop.wait(), timeout=delay)
            if self.stop.is_set():
                return
            body = {k: v for k, v in {"rpm": change.rpm, "tpm": change.tpm}.items() if v}
            await self._json(
                s, "PATCH", f"{self.cfg.gateway_url}/v1/models/{change.model}/limits", json=body
            )
            self.limit_change_log.append({"ts": time.time(), "model": change.model, **body})

    # ---------- drain + collect ----------

    async def _drain(self, s: aiohttp.ClientSession, receiver: CallbackReceiver | None) -> bool:
        deadline = time.time() + self.cfg.drain_timeout_seconds
        while time.time() < deadline and not self.stop.is_set():
            stats = await self._json(s, "GET", f"{self.cfg.gateway_url}/v1/stats")
            idle = stats["accounting"]["waiting"] == 0
            callbacks_done = True
            if receiver is not None and idle:
                pending = [b for b in self.batches if b not in receiver.received]
                for batch_id in pending[:50]:
                    b = await self._json(s, "GET", f"{self.cfg.gateway_url}/v1/batches/{batch_id}")
                    if b["callback_status"] not in FINAL_CALLBACK:
                        callbacks_done = False
                        break
            if idle and callbacks_done:
                return True
            await asyncio.sleep(0.5)
        return False

    async def _all_results(self, s: aiohttp.ClientSession, batch_id: str) -> list[dict]:
        items: list[dict] = []
        cursor = None
        while True:
            params = {"limit": "5000", **({"cursor": cursor} if cursor else {})}
            page = await self._json(
                s, "GET", f"{self.cfg.gateway_url}/v1/batches/{batch_id}/results", params=params
            )
            items += page["items"]
            cursor = page["next_cursor"]
            if not cursor:
                return items

    async def run(self) -> dict[str, Any]:
        cfg = self.cfg
        timeout = aiohttp.ClientTimeout(total=120)
        connector = aiohttp.TCPConnector(limit=cfg.max_concurrent_posts + 16)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as s:
            gw, sim = cfg.gateway_url, cfg.simulator_url
            health_before = await self._json(s, "GET", f"{gw}/healthz")
            stats_before = (await self._json(s, "GET", f"{gw}/v1/stats"))["accounting"]
            for model, limits in cfg.set_limits.items():
                body = limits.model_dump(exclude_none=True)
                await self._json(s, "PATCH", f"{gw}/v1/models/{model}/limits", json=body)
            for model, settings in cfg.simulator.items():
                await self._json(s, "PUT", f"{sim}/sim/models/{model}", json=settings)
            await self._json(s, "POST", f"{sim}/sim/reset")

            receiver = None
            if cfg.callback:
                receiver = CallbackReceiver(
                    cfg.callback_host, cfg.callback_port, cfg.callback_fail_first_n
                )
                await receiver.start()
            try:
                t0 = time.time()
                changes = asyncio.create_task(self._limit_changes(s, t0))
                await self._submit(s, receiver.url if receiver else None, t0)
                load_end = time.time()
                drained = await self._drain(s, receiver)
                changes.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await changes
                end = time.time()
                self.aborted = self.stop.is_set()

                stats_after = (await self._json(s, "GET", f"{gw}/v1/stats"))["accounting"]
                health_after = await self._json(s, "GET", f"{gw}/healthz")
                ts = await self._json(
                    s,
                    "GET",
                    f"{gw}/v1/metrics/timeseries",
                    params={"since": str(t0 - 1), "until": str(end)},
                )
                latency_all = await self._json(
                    s,
                    "GET",
                    f"{gw}/v1/metrics/latency",
                    params={"since": str(t0), "until": str(end)},
                )
                lo, hi = steady_window(t0, cfg.warmup_seconds, load_end, end)
                latency_steady = await self._json(
                    s,
                    "GET",
                    f"{gw}/v1/metrics/latency",
                    params={"since": str(lo), "until": str(hi)},
                )
                latency_call = await self._json(
                    s,
                    "GET",
                    f"{gw}/v1/metrics/latency",
                    params={"since": str(lo), "until": str(hi), "kind": "provider_call"},
                )
                referee = await self._json(s, "GET", f"{sim}/sim/stats")

                batch_ids = list(self.batches)
                if len(batch_ids) > cfg.batch_summaries_max > 0:
                    step = len(batch_ids) / cfg.batch_summaries_max
                    batch_ids = [batch_ids[int(i * step)] for i in range(cfg.batch_summaries_max)]
                elif cfg.batch_summaries_max == 0:
                    batch_ids = []
                batches = []
                for batch_id in batch_ids:
                    summary = await self._json(s, "GET", f"{gw}/v1/batches/{batch_id}")
                    entry = {**self.batches[batch_id], "summary": summary}
                    if receiver is not None:
                        entry["callback"] = receiver.received.get(batch_id)
                        entry["callback_attempts_seen"] = receiver.attempts.get(batch_id, 0)
                    if cfg.verify_results:
                        items = await self._all_results(s, batch_id)
                        entry["result_ids"] = [i["request_id"] for i in items]
                        entry["result_states"] = [i["state"] for i in items]
                        entry["result_finished_max"] = max(
                            (i["finished_at"] for i in items if i["finished_at"]), default=None
                        )
                    batches.append(entry)
            finally:
                if receiver is not None:
                    await receiver.stop()

        return {
            "config": cfg.model_dump(mode="json"),
            "times": {"start": t0, "load_end": load_end, "end": end, "drained": drained},
            "aborted": self.aborted,
            "client": {
                "sent_requests": self.sent_requests,
                "http_status": self.http_status,
                "http_errors": self.http_errors,
                "http_error_requests": self.http_error_requests,
                "ack_ms": self.ack_ms,
                "batches_submitted": len(self.batches),
            },
            "health": {"before": health_before, "after": health_after},
            "accounting": {"before": stats_before, "after": stats_after},
            "timeseries": ts["rows"],
            "limit_changes": ts["limit_changes"],
            "scheduled_changes": self.limit_change_log,
            "latency": {
                "all": latency_all,
                "steady": latency_steady,
                "provider_call": latency_call,
            },
            "referee": referee,
            "batches": batches,
        }
