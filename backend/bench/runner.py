"""Runs the benchmark engine on N processes and writes results/bench/<timestamp>/."""

import json
import multiprocessing as mp
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.bench.engine import run_shard
from backend.metrics.collector import percentile


class BenchConfig(BaseModel):
    shards: int = Field(8, ge=1)
    rate: float = Field(400_000, gt=0)  # total requests/second offered
    duration_seconds: float = Field(20, gt=0)
    rpm: int = Field(30_000_000, gt=0)  # total limit, split evenly across shards
    tpm: int = Field(30_000_000_000, gt=0)
    tokens: int = Field(1000, gt=0)
    latency_median_ms: float = Field(200, ge=0)
    latency_sigma: float = Field(0.5, ge=0)
    transient_error_rate: float = Field(0.0, ge=0, le=1)
    permanent_error_rate: float = Field(0.0, ge=0, le=1)
    max_attempts: int = Field(4, ge=1)
    max_queue: int = Field(5_000_000, gt=0)  # per shard
    drain_seconds: float = Field(5, ge=0)
    warmup_seconds: float = Field(2, ge=0)


def _shard(args: tuple[int, dict]) -> dict:
    return run_shard(*args)


def run_bench(cfg: BenchConfig, results_dir: str = "results") -> dict[str, Any]:
    from loadgen.environment import machine_info

    payload = {**cfg.model_dump(), "start_at": time.time() + 3}
    ctx = mp.get_context("spawn")
    with ctx.Pool(cfg.shards) as pool:
        shards = pool.map(_shard, [(i, payload) for i in range(cfg.shards)])

    start = payload["start_at"]
    load_end = start + cfg.duration_seconds
    completed = Counter()
    dispatched = Counter()
    for s in shards:
        completed.update({int(k): v for k, v in s["completed_per_s"].items()})
        dispatched.update({int(k): v for k, v in s["dispatched_per_s"].items()})

    steady = [
        completed.get(sec, 0) for sec in range(int(start + cfg.warmup_seconds) + 1, int(load_end))
    ]
    sustained = sum(steady) / max(1, len(steady))
    seconds = sorted(dispatched)
    window_max = max(
        (sum(dispatched.get(x, 0) for x in range(sec - 59, sec + 1)) for sec in seconds),
        default=0,
    )
    lat = sorted(x for s in shards for x in s["latencies"])
    totals = {
        k: sum(s[k] for s in shards)
        for k in ("generated", "rejected", "succeeded", "failed", "left_waiting")
    }
    totals["completed"] = totals["succeeded"] + totals["failed"]

    summary = {
        "sustained_completed_per_second": round(sustained),
        "peak_completed_per_second": max(steady, default=0),
        "offered_rate_per_second": cfg.rate,
        "required_300k_per_second": sustained >= 300_000,
        "stretch_1m_per_second": sustained >= 1_000_000,
        **totals,
        "latency_ms": {
            "p50": percentile(lat, 50),
            "p95": percentile(lat, 95),
            "p99": percentile(lat, 99),
        },
        "limit_total_rpm": cfg.rpm,
        "max_dispatched_in_any_aligned_60s": window_max,
        "per_shard_referee_max_vs_share": [
            [s["referee_max_requests_in_60s"], s["rpm_share"]] for s in shards
        ],
        "referee_violations": sum(s["referee_violations"] for s in shards),
        "accounted": totals["generated"]
        == totals["completed"] + totals["rejected"] + totals["left_waiting"],
    }
    report = {
        "kind": "bench",
        "started_at": datetime.fromtimestamp(start, UTC).isoformat(),
        "config": cfg.model_dump(),
        "environment": machine_info(),
        "summary": summary,
        "completed_per_second": [[sec, completed[sec]] for sec in sorted(completed)],
        "scope": (
            "MEASURED in-process benchmark: real limiter + real simulator core per request, "
            "multiprocess shards each owning 1/N of the limit. No HTTP, Redis or Postgres on "
            "this path; provider latency simulated with a 10 ms timer wheel; referee uses "
            "100 ms buckets."
        ),
    }
    out = Path(results_dir) / "bench" / datetime.fromtimestamp(start, UTC).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2))
    (out / "report.md").write_text(_markdown(report))
    _chart(report, out / "throughput.png")
    return {**report, "report_dir": str(out)}


def _markdown(r: dict) -> str:
    s, c, e = r["summary"], r["config"], r["environment"]
    lat = s["latency_ms"]
    return "\n".join(
        [
            "# Benchmark report — high-throughput simulation",
            "",
            f"> {r['scope']}",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Sustained completed / s (after {c['warmup_seconds']} s warm-up) | "
            f"**{s['sustained_completed_per_second']:,}** |",
            f"| Peak completed / s | {s['peak_completed_per_second']:,} |",
            f"| Offered rate / s | {s['offered_rate_per_second']:,.0f} |",
            f"| Required 300k/s met | {'✅' if s['required_300k_per_second'] else '❌'} |",
            f"| Stretch 1M/s met | {'✅' if s['stretch_1m_per_second'] else '❌'} |",
            f"| Completed (ok / failed) | {s['completed']:,} "
            f"({s['succeeded']:,} / {s['failed']:,}) |",
            f"| Rejected (queue full) / left waiting | {s['rejected']:,} / {s['left_waiting']:,} |",
            f"| Every request accounted for | {'✅' if s['accounted'] else '❌'} |",
            f"| Latency p50 / p95 / p99 (ms) | {lat['p50']:.0f} / {lat['p95']:.0f}"
            f" / {lat['p99']:.0f} |",
            f"| Total limit | {c['rpm']:,} RPM across {c['shards']} shards |",
            f"| Max dispatched in any aligned 60 s | {s['max_dispatched_in_any_aligned_60s']:,} |",
            f"| Referee violations | {s['referee_violations']} |",
            "",
            f"Environment: {e['cpu']}, {e['cpu_count']} cores, {e['ram_gb']} GB RAM, "
            f"{e['os']}, Python {e['python']}",
            "",
            "![throughput](throughput.png)",
            "",
        ]
    )


def _chart(r: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = r["completed_per_second"]
    if not pts:
        return
    t0 = pts[0][0]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot([p[0] - t0 for p in pts], [p[1] for p in pts], label="completed / s")
    ax.axhline(300_000, color="red", linestyle="--", linewidth=0.8, label="300k/s target")
    ax.set_xlabel("seconds")
    ax.set_ylabel("completed requests / s")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
