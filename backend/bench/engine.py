"""High-throughput benchmark engine - one shard (one process).

What it runs per request, with the same code as the service:
  * SlidingWindowLimiter.try_acquire   (the real limiter, on this shard's share of the limit)
  * ProviderSimulator.handle           (the real simulator core: referee, latency, outcomes)
  * retry of transient failures, latency measured from submission to final state

What it deliberately skips (stated in the report): HTTP, Redis and Postgres. Requests are
generated in-process at a fixed rate (open loop), and provider latency is simulated with a
timer wheel (10 ms slots) instead of one sleeping task per request.

A request counts as completed only when its simulated provider call reaches a final state.
"""

import math
import random
import time
from collections import Counter, deque
from typing import Any

from backend.dispatcher.limiter import SlidingWindowLimiter
from backend.simulator.config import SimModelConfig
from backend.simulator.provider import ProviderSimulator
from backend.simulator.referee import BucketWindow

CHUNK = 20_000  # max dispatches per loop iteration (keeps completions flowing)
SLOT = 0.01  # timer-wheel resolution (seconds)
LATENCY_SAMPLES = 20_000


def run_shard(shard: int, cfg: dict[str, Any]) -> dict[str, Any]:
    n = cfg["shards"]
    rate = cfg["rate"] / n
    rpm, tpm = cfg["rpm"] // n, cfg["tpm"] // n
    tokens, max_attempts = cfg["tokens"], cfg["max_attempts"]

    limiter = SlidingWindowLimiter(rpm, tpm, utilization=0.98, burst_factor=1.0)
    sim = ProviderSimulator(
        [
            SimModelConfig(
                name="bench",
                rpm=rpm,
                tpm=tpm,
                latency_median_ms=cfg["latency_median_ms"],
                latency_sigma=cfg["latency_sigma"],
                transient_error_rate=cfg["transient_error_rate"],
                permanent_error_rate=cfg["permanent_error_rate"],
                enforce_limits=False,  # observe mode: count violations instead of 429s
            )
        ],
        seed=shard,
        window_factory=BucketWindow,
    )
    rng = random.Random(1000 + shard)

    queue: deque[tuple[float, int]] = deque()
    wheel: dict[int, list[tuple[float, int, int]]] = {}
    dispatched_per_s: Counter[int] = Counter()
    completed_per_s: Counter[int] = Counter()
    latencies: list[float] = []
    seen_latencies = 0
    generated = succeeded = failed = rejected = 0

    start = cfg["start_at"]
    while time.time() < start:  # all shards start together
        time.sleep(0.001)
    load_end = start + cfg["duration_seconds"]
    hard_end = load_end + cfg["drain_seconds"]
    next_slot = math.floor(start / SLOT)

    while True:
        now = time.time()
        if now >= hard_end:
            break

        # 1. arrivals (open loop)
        if now < load_end:
            new = int((now - start) * rate) - generated
            if new > 0:
                room = cfg["max_queue"] - len(queue)
                accepted = min(new, max(0, room))
                rejected += new - accepted
                queue.extend([(now, 0)] * accepted)
                generated += new
        elif not queue and not wheel:
            break

        # 2. dispatch through the limiter + simulator
        sent = 0
        while queue and sent < CHUNK:
            if not limiter.try_acquire(tokens, now).allowed:
                break
            submitted, attempts = queue.popleft()
            out = sim.handle("bench", tokens, now)
            slot = math.floor((now + out.latency_ms / 1000) / SLOT)
            wheel.setdefault(slot, []).append((submitted, attempts + 1, out.status_code))
            sent += 1
        if sent:
            dispatched_per_s[math.floor(now)] += sent

        # 3. completions whose simulated latency has elapsed
        current = math.floor(now / SLOT)
        second = math.floor(now)
        for slot in range(next_slot, current + 1):
            items = wheel.pop(slot, None)
            if not items:
                continue
            for submitted, attempts, code in items:
                if code == 503 and attempts < max_attempts:
                    queue.appendleft((submitted, attempts))  # retry (back through the limiter)
                    continue
                if code == 200:
                    succeeded += 1
                else:
                    failed += 1
                completed_per_s[second] += 1
                seen_latencies += 1
                lat = (now - submitted) * 1000
                if len(latencies) < LATENCY_SAMPLES:
                    latencies.append(lat)
                else:
                    j = rng.randrange(seen_latencies)
                    if j < LATENCY_SAMPLES:
                        latencies[j] = lat
        next_slot = current  # re-check the current slot: new items may still land in it

        if not sent and not queue:
            time.sleep(0.0005)  # idle: don't spin

    stats = sim.stats(now=time.time())["bench"]
    return {
        "shard": shard,
        "rpm_share": rpm,
        "tpm_share": tpm,
        "generated": generated,
        "rejected": rejected,
        "succeeded": succeeded,
        "failed": failed,
        "left_waiting": len(queue) + sum(len(v) for v in wheel.values()),
        "dispatched_per_s": dict(dispatched_per_s),
        "completed_per_s": dict(completed_per_s),
        "latencies": latencies,
        "referee_max_requests_in_60s": stats["max_requests_in_any_60s"],
        "referee_violations": stats["violations"],
    }
