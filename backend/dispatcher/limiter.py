"""Per-model sliding-window rate limiter for RPM *and* TPM.

How it works
------------
Usage is counted in one-second buckets kept in a ring. A request is allowed only if, after
adding it, both the request count and the token count over the window stay within the limit.

Why 61 buckets and not 60
-------------------------
The rule is "no 60-second period may exceed the limit" - *any* period, e.g. 12:00:00.7 to
12:01:00.7. With whole-second buckets, the period (now-60, now] can start part-way through the
bucket from 60 seconds ago. Counting that whole bucket too (61 buckets = seconds s-60 .. s)
makes the check conservative, so no real 60-second period can ever exceed the limit.
The cost is at most ~1/61 (1.6%) of capacity.

Smooth pacing
-------------
With `burst_factor=1.0` at most ceil(limit/60) requests (and tokens) go out per second, released
in 100 ms slices, so a backlog drains as a steady stream instead of a burst at the start of each
minute (or each second). Bursts matter: they queue in the HTTP connection pool, and requests
that arrive late at the provider can land after a limit change.

Single-threaded (asyncio) use only. Multi-process scaling splits a model's limit into
per-process shares instead of sharing one limiter (see later commits).
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from backend.common.schemas import ModelConfig

WINDOW_SECONDS = 60
BUCKETS = WINDOW_SECONDS + 1
PACING_SLICES = 10  # each second's share is released in 100 ms steps


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    retry_after: float = 0.0  # seconds until it may fit; math.inf = never at current limits


class SlidingWindowLimiter:
    def __init__(
        self,
        rpm: int,
        tpm: int,
        *,
        utilization: float = 1.0,
        burst_factor: float | None = 1.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not 0 < utilization <= 1:
            raise ValueError("utilization must be in (0, 1]")
        if burst_factor is not None and burst_factor <= 0:
            raise ValueError("burst_factor must be > 0")
        self._clock = clock
        self._utilization = utilization
        self._burst_factor = burst_factor
        self._req = [0] * BUCKETS
        self._tok = [0] * BUCKETS
        self._req_sum = 0
        self._tok_sum = 0
        self._head: int | None = None  # most recent second the ring has advanced to
        self.rpm = rpm
        self.tpm = tpm
        self.update_limits(rpm=rpm, tpm=tpm)

    # ---------- limits ----------

    def update_limits(self, rpm: int | None = None, tpm: int | None = None) -> None:
        """Change limits at runtime. Usage already in the window is kept, so after a decrease
        nothing new is allowed until the window drains below the new limit."""
        if rpm is not None:
            if rpm <= 0:
                raise ValueError("rpm must be > 0")
            self.rpm = rpm
        if tpm is not None:
            if tpm <= 0:
                raise ValueError("tpm must be > 0")
            self.tpm = tpm
        self.effective_rpm = max(1, math.floor(self.rpm * self._utilization))
        self.effective_tpm = max(1, math.floor(self.tpm * self._utilization))
        if self._burst_factor is None:
            self.per_second_requests: int | None = None
            self.per_second_tokens: int | None = None
        else:
            self.per_second_requests = max(
                1, math.ceil(self.effective_rpm * self._burst_factor / WINDOW_SECONDS)
            )
            self.per_second_tokens = max(
                1, math.ceil(self.effective_tpm * self._burst_factor / WINDOW_SECONDS)
            )

    # ---------- window bookkeeping ----------

    def _advance(self, now: float) -> int:
        """Move the ring forward to the current second, clearing expired buckets."""
        second = math.floor(now)
        if self._head is None:
            self._head = second
            return second
        if second <= self._head:  # same second (or clock went backwards): use current bucket
            return self._head
        steps = min(second - self._head, BUCKETS)
        for i in range(1, steps + 1):
            idx = (self._head + i) % BUCKETS
            self._req_sum -= self._req[idx]
            self._tok_sum -= self._tok[idx]
            self._req[idx] = 0
            self._tok[idx] = 0
        self._head = second
        return second

    def usage(self, now: float | None = None) -> tuple[int, int]:
        """(requests, tokens) currently counted in the window."""
        self._advance(self._clock() if now is None else now)
        return self._req_sum, self._tok_sum

    def fits_ever(self, tokens: int) -> bool:
        """False if a single request is bigger than the whole token limit."""
        return tokens <= self.effective_tpm

    # ---------- main API ----------

    def try_acquire(self, tokens: int, now: float | None = None) -> Decision:
        """Reserve 1 request + `tokens` if the window allows it right now."""
        if tokens < 0:
            raise ValueError("tokens must be >= 0")
        t = self._clock() if now is None else now
        second = self._advance(t)

        if not self.fits_ever(tokens):
            return Decision(False, math.inf)

        if self._req_sum + 1 > self.effective_rpm or self._tok_sum + tokens > self.effective_tpm:
            return Decision(False, self._time_until_fits(tokens, t, second))

        idx = second % BUCKETS
        if self.per_second_requests is not None and self.per_second_tokens is not None:
            # Spread each second's share over PACING_SLICES slices, so a backlog doesn't go out
            # as one burst at the start of every second.
            into = t - second
            share = min(1.0, (math.floor(into * PACING_SLICES) + 1) / PACING_SLICES)
            over_requests = self._req[idx] + 1 > math.ceil(self.per_second_requests * share)
            # a lone request bigger than the per-second token share is still allowed
            over_tokens = self._req[idx] > 0 and self._tok[idx] + tokens > math.ceil(
                self.per_second_tokens * share
            )
            if over_requests or over_tokens:
                next_slice = (math.floor(into * PACING_SLICES) + 1) / PACING_SLICES
                return Decision(False, max(0.001, next_slice - into))

        self._req[idx] += 1
        self._tok[idx] += tokens
        self._req_sum += 1
        self._tok_sum += tokens
        return Decision(True)

    def _time_until_fits(self, tokens: int, t: float, second: int) -> float:
        """Seconds until enough old buckets leave the window for this request to fit."""
        freed_req = freed_tok = 0
        for bucket_second in range(second - WINDOW_SECONDS, second + 1):
            idx = bucket_second % BUCKETS
            freed_req += self._req[idx]
            freed_tok += self._tok[idx]
            fits_requests = self._req_sum - freed_req + 1 <= self.effective_rpm
            fits_tokens = self._tok_sum - freed_tok + tokens <= self.effective_tpm
            if fits_requests and fits_tokens:
                return max(0.0, (bucket_second + BUCKETS) - t)
        return max(0.0, (second + BUCKETS) - t)


class UnknownModelError(KeyError):
    pass


class LimiterRegistry:
    """One independent limiter per model, so a constrained model never blocks another."""

    def __init__(
        self,
        models: dict[str, ModelConfig],
        *,
        utilization: float = 1.0,
        burst_factor: float | None = 1.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._utilization = utilization
        self._burst_factor = burst_factor
        self._clock = clock
        self._limiters: dict[str, SlidingWindowLimiter] = {}
        for cfg in models.values():
            self.add_model(cfg)

    def add_model(self, cfg: ModelConfig) -> None:
        self._limiters[cfg.name] = SlidingWindowLimiter(
            cfg.rpm,
            cfg.tpm,
            utilization=self._utilization,
            burst_factor=self._burst_factor,
            clock=self._clock,
        )

    def get(self, model: str) -> SlidingWindowLimiter:
        try:
            return self._limiters[model]
        except KeyError:
            raise UnknownModelError(model) from None

    def update_limits(self, model: str, rpm: int | None = None, tpm: int | None = None) -> None:
        self.get(model).update_limits(rpm=rpm, tpm=tpm)

    def snapshot(self, now: float | None = None) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for name, lim in self._limiters.items():
            used_requests, used_tokens = lim.usage(now)
            out[name] = {
                "rpm": lim.rpm,
                "tpm": lim.tpm,
                "effective_rpm": lim.effective_rpm,
                "effective_tpm": lim.effective_tpm,
                "window_requests": used_requests,
                "window_tokens": used_tokens,
            }
        return out
