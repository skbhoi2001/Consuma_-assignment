import time

from backend.bench.engine import run_shard
from backend.simulator.referee import BucketWindow


def test_bucket_window_tracks_rolling_counts():
    w = BucketWindow(window_seconds=1.0, resolution=0.1)
    for i in range(10):
        w.add(i * 0.1, 5)
    assert w.current(0.95) == (10, 50)
    assert w.current(1.05) == (9, 45)  # the 0.0 bucket has left
    assert w.max_requests == 10 and w.would_exceed(1.05, 1, rpm=9, tpm=1000)
    assert w.retry_after(1.05) > 0


def base_cfg(**kw):
    cfg = dict(
        shards=1,
        rate=2_000,
        duration_seconds=1.0,
        rpm=600_000,
        tpm=600_000_000,
        tokens=10,
        latency_median_ms=5,
        latency_sigma=0.2,
        transient_error_rate=0.0,
        permanent_error_rate=0.0,
        max_attempts=4,
        max_queue=1_000_000,
        drain_seconds=2.0,
        start_at=time.time(),
    )
    cfg.update(kw)
    return cfg


def test_shard_completes_everything_and_accounts():
    r = run_shard(0, base_cfg(transient_error_rate=0.2, permanent_error_rate=0.05))
    assert r["generated"] >= 1_900
    assert r["left_waiting"] == 0
    assert r["succeeded"] + r["failed"] + r["rejected"] == r["generated"]
    assert r["failed"] > 0 and r["referee_violations"] == 0


def test_shard_respects_its_limit_share():
    r = run_shard(0, base_cfg(rate=5_000, rpm=600, drain_seconds=0.5))  # far above the limit
    assert sum(r["dispatched_per_s"].values()) <= 600
    assert r["referee_max_requests_in_60s"] <= 600
    assert r["left_waiting"] > 0  # the rest is still queued when the run ends
