import math
import random

import pytest

from backend.common.schemas import ModelConfig
from backend.dispatcher.limiter import (
    LimiterRegistry,
    SlidingWindowLimiter,
    UnknownModelError,
)
from backend.simulator.referee import ExactWindow


def limiter(rpm=100, tpm=1_000_000, **kw):
    kw.setdefault("burst_factor", None)  # most tests check the window rule alone
    return SlidingWindowLimiter(rpm, tpm, clock=lambda: 0.0, **kw)


def fill(lim, n, tokens, now):
    return sum(lim.try_acquire(tokens, now=now).allowed for _ in range(n))


def test_allows_up_to_rpm_then_denies():
    lim = limiter(rpm=100)
    assert fill(lim, 150, 10, now=5.0) == 100
    d = lim.try_acquire(10, now=5.0)
    assert not d.allowed and d.retry_after > 0


def test_minute_boundary_burst_is_blocked():
    """The Scenario 1 trap: a fixed per-minute counter would allow 2x across a boundary."""
    lim = limiter(rpm=100)
    assert fill(lim, 100, 1, now=59.9) == 100
    # A fixed window would reset at 60.0. The real period (0.5, 60.5] still holds 100 requests.
    assert fill(lim, 10, 1, now=60.5) == 0
    assert fill(lim, 10, 1, now=119.5) == 0
    # The 59.9 bucket (second 59) leaves the 61-bucket window at second 120.
    assert fill(lim, 100, 1, now=120.0) == 100


def test_tpm_can_be_the_binding_limit():
    lim = limiter(rpm=1_000, tpm=100_000)
    assert fill(lim, 50, 5_000, now=1.0) == 20  # 20 x 5k tokens = 100k


def test_retry_after_points_to_when_capacity_frees():
    lim = limiter(rpm=10)
    fill(lim, 10, 1, now=10.2)  # all in second 10 -> leaves window at second 71
    d = lim.try_acquire(1, now=20.0)
    assert not d.allowed
    assert d.retry_after == pytest.approx(51.0)
    assert lim.try_acquire(1, now=20.0 + d.retry_after).allowed


def test_request_bigger_than_tpm_never_fits():
    lim = limiter(rpm=100, tpm=1_000)
    d = lim.try_acquire(5_000, now=1.0)
    assert not d.allowed and math.isinf(d.retry_after)
    assert not lim.fits_ever(5_000)


def test_utilization_headroom():
    lim = limiter(rpm=100, utilization=0.9)
    assert lim.effective_rpm == 90
    assert fill(lim, 100, 1, now=1.0) == 90


def test_limit_decrease_waits_for_window_to_drain_then_increase_applies_immediately():
    lim = limiter(rpm=100)
    assert fill(lim, 30, 1, now=1.0) == 30
    lim.update_limits(rpm=20)  # window already holds 30 > 20
    assert fill(lim, 5, 1, now=30.0) == 0
    assert fill(lim, 30, 1, now=62.0) == 20  # second 1 has left the window
    lim.update_limits(rpm=100)
    assert fill(lim, 100, 1, now=62.0) == 80  # 20 used + 80 more = 100


def test_per_second_pacing():
    lim = limiter(rpm=600, burst_factor=1.0)  # 600/min -> 10 per second
    assert lim.per_second_requests == 10
    assert fill(lim, 20, 1, now=1.0) == 10
    d = lim.try_acquire(1, now=1.4)
    assert not d.allowed and d.retry_after == pytest.approx(0.6)
    assert fill(lim, 20, 1, now=2.0) == 10


def test_lone_large_request_allowed_despite_per_second_token_share():
    lim = limiter(rpm=600, tpm=6_000, burst_factor=1.0)  # 100 tokens/second share
    assert lim.try_acquire(1_000, now=1.0).allowed  # alone in its second -> allowed
    assert not lim.try_acquire(1, now=1.0).allowed  # second already over its token share


def test_invalid_arguments():
    with pytest.raises(ValueError):
        SlidingWindowLimiter(0, 10)
    with pytest.raises(ValueError):
        SlidingWindowLimiter(10, 10, utilization=1.5)
    with pytest.raises(ValueError):
        limiter().try_acquire(-1, now=0.0)


@pytest.mark.parametrize("burst_factor", [None, 1.0, 2.0])
def test_never_exceeds_any_real_60s_window_randomized(burst_factor):
    """Greedy random traffic for 10 simulated minutes, checked by the exact referee."""
    rng = random.Random(42)
    rpm, tpm = 500, 200_000
    lim = SlidingWindowLimiter(rpm, tpm, burst_factor=burst_factor, clock=lambda: 0.0)
    referee = ExactWindow()
    t = 0.0
    while t < 600:
        t += rng.expovariate(20)  # ~20 attempts per second, far above the limit
        tokens = rng.randint(100, 900)
        if lim.try_acquire(tokens, now=t).allowed:
            assert not referee.would_exceed(t, tokens, rpm, tpm), f"limit exceeded at t={t:.3f}"
            referee.add(t, tokens)
    assert referee.max_requests <= rpm
    assert referee.max_tokens <= tpm


def test_backlog_uses_at_least_90_percent_of_capacity():
    """Scenario 1 shape: demand far above the limit, steady state after warm-up."""
    rpm = 6_000
    lim = SlidingWindowLimiter(rpm, 10**9, utilization=0.98, burst_factor=1.0, clock=lambda: 0.0)
    sent_after_warmup = 0
    t = 0.0
    while t < 300:  # 5 minutes, try every 10 ms
        while lim.try_acquire(1_000, now=t).allowed:
            if t >= 60:
                sent_after_warmup += 1
        t = round(t + 0.01, 2)
    per_minute = sent_after_warmup / 4
    assert per_minute >= 0.9 * rpm
    assert per_minute <= rpm


def test_registry_keeps_models_isolated():
    models = {
        "a": ModelConfig(name="a", rpm=10, tpm=10_000),
        "b": ModelConfig(name="b", rpm=10, tpm=10_000),
    }
    reg = LimiterRegistry(models, burst_factor=None, clock=lambda: 0.0)
    assert fill(reg.get("a"), 20, 1, now=1.0) == 10
    assert fill(reg.get("b"), 5, 1, now=1.0) == 5  # a being full doesn't affect b
    reg.update_limits("a", rpm=5)
    assert reg.get("b").rpm == 10
    snap = reg.snapshot(now=1.0)
    assert snap["a"]["window_requests"] == 10 and snap["b"]["window_requests"] == 5
    with pytest.raises(UnknownModelError):
        reg.get("missing")
