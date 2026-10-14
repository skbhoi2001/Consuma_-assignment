"""Provider simulator core: decides the outcome of one inference call.

Pure Python with no I/O, so it can run behind HTTP (app.py) or in-process (benchmarks).
"""

import math
import random
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from backend.simulator.config import SimModelConfig, SimModelUpdate
from backend.simulator.referee import ExactWindow


@dataclass(frozen=True, slots=True)
class SimOutcome:
    status_code: int  # 200 | 400 | 404 | 429 | 503
    kind: str  # success | permanent_error | unknown_model | rate_limited | transient_error
    latency_ms: float = 0.0
    actual_tokens: int | None = None
    retry_after: float | None = None


@dataclass
class ModelStats:
    accepted: int = 0  # calls counted against the limits (everything except 429s)
    succeeded: int = 0
    transient_errors: int = 0
    permanent_errors: int = 0
    rate_limited: int = 0  # answered 429 because the call would exceed RPM/TPM
    violations: int = 0  # accepted while over the limit (only when enforce_limits=false)
    window: ExactWindow = field(default_factory=ExactWindow)


class ProviderSimulator:
    def __init__(
        self,
        models: list[SimModelConfig],
        *,
        seed: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._models = {m.name: m for m in models}
        self._stats = {m.name: ModelStats() for m in models}
        self._rng = random.Random(seed)
        self._clock = clock

    @property
    def models(self) -> dict[str, SimModelConfig]:
        return dict(self._models)

    def handle(self, model: str, estimated_tokens: int, now: float | None = None) -> SimOutcome:
        cfg = self._models.get(model)
        if cfg is None:
            return SimOutcome(404, "unknown_model")

        t = self._clock() if now is None else now
        stats = self._stats[model]
        over_limit = stats.window.would_exceed(t, estimated_tokens, cfg.rpm, cfg.tpm)

        if over_limit and cfg.enforce_limits:
            stats.rate_limited += 1
            return SimOutcome(429, "rate_limited", retry_after=stats.window.retry_after(t))
        if over_limit:
            stats.violations += 1

        stats.window.add(t, estimated_tokens)
        stats.accepted += 1
        latency = self._latency_ms(cfg)

        roll = self._rng.random()
        if roll < cfg.permanent_error_rate:
            stats.permanent_errors += 1
            return SimOutcome(400, "permanent_error", latency)
        if roll < cfg.permanent_error_rate + cfg.transient_error_rate:
            stats.transient_errors += 1
            return SimOutcome(503, "transient_error", latency)

        stats.succeeded += 1
        actual = max(1, round(estimated_tokens * self._rng.uniform(0.9, 1.1)))
        return SimOutcome(200, "success", latency, actual_tokens=actual)

    def _latency_ms(self, cfg: SimModelConfig) -> float:
        if cfg.latency_median_ms <= 0:
            return 0.0
        return self._rng.lognormvariate(math.log(cfg.latency_median_ms), cfg.latency_sigma)

    def update_model(self, name: str, update: SimModelUpdate) -> SimModelConfig:
        """Change a model's settings at runtime; the observed window is kept."""
        current = self._models[name]  # KeyError if unknown
        merged = SimModelConfig(**{**current.model_dump(), **update.model_dump(exclude_unset=True)})
        self._models[name] = merged
        return merged

    def stats(self, now: float | None = None) -> dict[str, dict]:
        t = self._clock() if now is None else now
        out: dict[str, dict] = {}
        for name, s in self._stats.items():
            cfg = self._models[name]
            window_requests, window_tokens = s.window.current(t)
            counters = {k: v for k, v in asdict(s).items() if k != "window"}
            out[name] = {
                "rpm": cfg.rpm,
                "tpm": cfg.tpm,
                "enforce_limits": cfg.enforce_limits,
                **counters,
                "window_requests": window_requests,
                "window_tokens": window_tokens,
                "max_requests_in_any_60s": s.window.max_requests,
                "max_tokens_in_any_60s": s.window.max_tokens,
            }
        return out

    def reset(self) -> None:
        self._stats = {name: ModelStats() for name in self._models}
