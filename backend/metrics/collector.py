"""In-memory, per-second, per-model metrics.

Every second gets one bucket per model with what was *dispatched* (requests + tokens - this is
what counts against RPM/TPM), what *finished* (by final state), and a sample of end-to-end
latencies. The limit in force is stamped on each bucket, so reports can compare observed vs
configured over time - including while limits change (Scenario 2).

Kept for `retention_seconds` (default 1 hour). Also exported to Prometheus.
"""

import math
import random
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field

from prometheus_client import CollectorRegistry, Counter, Gauge

from backend.common.schemas import ModelConfig
from backend.common.states import RequestState

WINDOW = 60


def percentile(sorted_values: list[float], p: float) -> float | None:
    """Nearest-rank percentile of an already sorted list."""
    if not sorted_values:
        return None
    k = max(0, math.ceil(p / 100 * len(sorted_values)) - 1)
    return sorted_values[k]


@dataclass
class SecondStats:
    configured_rpm: int
    configured_tpm: int
    dispatched: int = 0  # provider calls started (incl. retries) - counts against RPM
    tokens: int = 0  # tokens of those calls - counts against TPM
    succeeded: int = 0
    failed: int = 0
    expired: int = 0
    rejected: int = 0
    retried: int = 0
    provider_429: int = 0
    latency_count: int = 0
    latencies: array = field(default_factory=lambda: array("f"))  # end-to-end, sampled, ms
    call_count: int = 0
    call_latencies: array = field(default_factory=lambda: array("f"))  # provider call, ms


_COUNTS = (
    "dispatched",
    "tokens",
    "succeeded",
    "failed",
    "expired",
    "rejected",
    "retried",
    "provider_429",
)


def _row(second: int, model: str, rpm: int, tpm: int, b: SecondStats | None) -> dict:
    row = {"ts": second, "model": model, "configured_rpm": rpm, "configured_tpm": tpm}
    for name in _COUNTS:
        row[name] = getattr(b, name) if b is not None else 0
    return row


class MetricsCollector:
    def __init__(
        self,
        models: dict[str, ModelConfig],
        *,
        retention_seconds: int = 3600,
        latency_samples_per_second: int = 2000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._retention = retention_seconds
        self._max_samples = latency_samples_per_second
        self._rng = random.Random(0)
        self._limits: dict[str, tuple[int, int]] = {n: (m.rpm, m.tpm) for n, m in models.items()}
        self._seconds: dict[str, dict[int, SecondStats]] = {n: {} for n in models}
        self.limit_changes: list[dict] = []

        self.registry = CollectorRegistry()
        self._p_dispatched = Counter(
            "gateway_dispatched_requests",
            "Provider calls started",
            ["model"],
            registry=self.registry,
        )
        self._p_tokens = Counter(
            "gateway_dispatched_tokens",
            "Tokens of provider calls",
            ["model"],
            registry=self.registry,
        )
        self._p_final = Counter(
            "gateway_final_requests",
            "Requests reaching a final state",
            ["model", "state"],
            registry=self.registry,
        )
        self._p_rpm = Gauge(
            "gateway_configured_rpm", "Configured RPM limit", ["model"], registry=self.registry
        )
        self._p_tpm = Gauge(
            "gateway_configured_tpm", "Configured TPM limit", ["model"], registry=self.registry
        )
        for name, (rpm, tpm) in self._limits.items():
            self._p_rpm.labels(name).set(rpm)
            self._p_tpm.labels(name).set(tpm)

    # ---------- recording ----------

    def _bucket(self, model: str, now: float) -> SecondStats | None:
        per_model = self._seconds.get(model)
        if per_model is None:
            return None
        second = math.floor(now)
        b = per_model.get(second)
        if b is None:
            rpm, tpm = self._limits[model]
            b = per_model[second] = SecondStats(rpm, tpm)
            cutoff = second - self._retention
            if len(per_model) > self._retention + 5:
                for old in [s for s in per_model if s < cutoff]:
                    del per_model[old]
        return b

    def on_dispatch(self, model: str, tokens: int, now: float | None = None) -> None:
        b = self._bucket(model, self._clock() if now is None else now)
        if b is not None:
            b.dispatched += 1
            b.tokens += tokens
            self._p_dispatched.labels(model).inc()
            self._p_tokens.labels(model).inc(tokens)

    def on_retry(self, model: str, provider_429: bool, now: float | None = None) -> None:
        b = self._bucket(model, self._clock() if now is None else now)
        if b is not None:
            b.retried += 1
            b.provider_429 += int(provider_429)

    def _sample(self, values: array, seen: int, value: float) -> None:
        if len(values) < self._max_samples:
            values.append(value)
        else:  # reservoir sampling keeps an unbiased sample
            j = self._rng.randrange(seen)
            if j < self._max_samples:
                values[j] = value

    def on_final(
        self,
        model: str,
        state: RequestState,
        latency_ms: float | None,
        now: float | None = None,
        call_ms: float | None = None,
    ) -> None:
        b = self._bucket(model, self._clock() if now is None else now)
        if b is None:
            return
        setattr(b, state.value, getattr(b, state.value) + 1)
        self._p_final.labels(model, state.value).inc()
        if state not in (RequestState.SUCCEEDED, RequestState.FAILED):
            return
        if latency_ms is not None:
            b.latency_count += 1
            self._sample(b.latencies, b.latency_count, latency_ms)
        if call_ms is not None:
            b.call_count += 1
            self._sample(b.call_latencies, b.call_count, call_ms)

    def on_limits(self, model: str, rpm: int, tpm: int, now: float | None = None) -> None:
        t = self._clock() if now is None else now
        old = self._limits.get(model)
        self._limits[model] = (rpm, tpm)
        self._seconds.setdefault(model, {})
        b = self._bucket(model, t)
        if b is not None:  # the current second shows the new limit
            b.configured_rpm, b.configured_tpm = rpm, tpm
        self._p_rpm.labels(model).set(rpm)
        self._p_tpm.labels(model).set(tpm)
        self.limit_changes.append({"ts": t, "model": model, "old": old, "rpm": rpm, "tpm": tpm})

    # ---------- reading ----------

    def timeseries(
        self, since: float | None = None, until: float | None = None, model: str | None = None
    ) -> list[dict]:
        """One row per model per second (gaps filled with zeros), oldest first."""
        now = math.floor(self._clock())
        end = math.floor(until) if until is not None else now
        rows: list[dict] = []
        for name, per_model in self._seconds.items():
            if model is not None and name != model:
                continue
            if not per_model:
                continue
            start = math.floor(since) if since is not None else min(per_model)
            start = max(start, min(per_model), end - self._retention)
            first = per_model[min(per_model)]
            rpm, tpm = first.configured_rpm, first.configured_tpm
            for second in range(start, end + 1):
                b = per_model.get(second)
                if b is not None:
                    rpm, tpm = b.configured_rpm, b.configured_tpm
                rows.append(_row(second, name, rpm, tpm, b))
        rows.sort(key=lambda r: (r["ts"], r["model"]))
        return rows

    def latency(
        self,
        since: float | None = None,
        until: float | None = None,
        model: str | None = None,
        kind: str = "end_to_end",
    ) -> dict[str, float | int | None]:
        """Latency percentiles over a time range, in ms.

        kind="end_to_end": submit -> final state (includes time waiting in the queue).
        kind="provider_call": duration of the final provider call only.
        """
        values_attr, count_attr = (
            ("call_latencies", "call_count")
            if kind == "provider_call"
            else ("latencies", "latency_count")
        )
        lo = math.floor(since) if since is not None else -math.inf
        hi = math.floor(until) if until is not None else math.inf
        samples: list[float] = []
        count = 0
        for name, per_model in self._seconds.items():
            if model is not None and name != model:
                continue
            for second, b in per_model.items():
                if lo <= second <= hi:
                    samples.extend(getattr(b, values_attr))
                    count += getattr(b, count_attr)
        samples.sort()
        return {
            "count": count,
            "sampled": len(samples),
            "p50": percentile(samples, 50),
            "p95": percentile(samples, 95),
            "p99": percentile(samples, 99),
        }

    def live(self, now: float | None = None) -> dict[str, dict]:
        """Per model: limits, rolling-60s observed usage, last-second rates, 60s latency."""
        t = math.floor(self._clock() if now is None else now)
        out: dict[str, dict] = {}
        for name, per_model in self._seconds.items():
            window = [per_model[s] for s in range(t - WINDOW + 1, t + 1) if s in per_model]
            last = per_model.get(t - 1)
            rpm, tpm = self._limits[name]
            lat = self.latency(since=t - WINDOW + 1, until=t, model=name)
            call = self.latency(since=t - WINDOW + 1, until=t, model=name, kind="provider_call")
            out[name] = {
                "configured_rpm": rpm,
                "configured_tpm": tpm,
                "rolling_60s_requests": sum(b.dispatched for b in window),
                "rolling_60s_tokens": sum(b.tokens for b in window),
                "completed_last_second": (last.succeeded + last.failed) if last else 0,
                "dispatched_last_second": last.dispatched if last else 0,
                "latency_p50_ms": lat["p50"],
                "latency_p95_ms": lat["p95"],
                "latency_p99_ms": lat["p99"],
                "provider_call_p50_ms": call["p50"],
                "provider_call_p99_ms": call["p99"],
            }
        return out
