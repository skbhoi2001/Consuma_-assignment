"""Turns collected run data into report.json, report.md and charts."""

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from backend.metrics.collector import percentile  # noqa: E402
from loadgen import checks as C  # noqa: E402
from loadgen.environment import machine_info  # noqa: E402

MEASURED_NOTE = (
    "All numbers in this report are MEASURED on the machine listed under Environment. "
    "The provider is the local simulator; no real LLM calls were made."
)


def _pcts(values: list[float]) -> dict[str, float | None]:
    v = sorted(values)
    return {"p50": percentile(v, 50), "p95": percentile(v, 95), "p99": percentile(v, 99)}


def build_report(data: dict[str, Any], run_id: str) -> dict[str, Any]:
    cfg, t, rows = data["config"], data["times"], data["timeseries"]
    start, load_end, end = t["start"], t["load_end"], t["end"]
    warm = start + cfg["warmup_seconds"]
    acc = C.delta(data["accounting"]["before"], data["accounting"]["after"])
    models = list(cfg["models"])

    results: list[dict] = []
    wanted = set(cfg["checks"])
    if "accounting" in wanted:
        results.append(
            C.check_accounting(
                acc, data["client"]["sent_requests"], data["client"]["http_error_requests"]
            )
        )
    if "limits_respected" in wanted:
        results += C.check_limits(rows, data["referee"], models)
    if "utilization" in wanted:
        for model in models:
            rpm = next((r["configured_rpm"] for r in rows if r["model"] == model), None)
            if rpm:
                results.append(C.check_utilization(rows, model, warm, load_end, rpm))
    if "scenario2" in wanted and len(models) >= 2:
        constrained = cfg["limit_changes"][0]["model"]
        other = next(m for m in models if m != constrained)
        other_rpm = cfg["set_limits"].get(other, {}).get("rpm") or next(
            r["configured_rpm"] for r in rows if r["model"] == other
        )
        # only the changes made during the run (not the initial set_limits)
        results += C.check_scenario2(rows, data["scheduled_changes"], constrained, other, other_rpm)
    if "no_restart" in wanted:
        results.append(C.check_no_restart(data["health"]))
    if "batches" in wanted:
        results += C.check_batches(
            data["batches"], cfg["callback_fail_first_n"], data["client"]["ack_ms"]
        )

    duration = max(1e-9, end - start)
    lo, hi = C.steady_window(start, cfg["warmup_seconds"], load_end, end)
    steady_completed = sum(r["succeeded"] + r["failed"] for r in rows if lo <= r["ts"] < hi)
    steady_secs = max(1.0, hi - lo)

    batch_timing = []
    for b in data["batches"]:
        s = b["summary"]
        if s.get("completed_at"):
            created = datetime.fromisoformat(s["created_at"]).timestamp()
            completed = datetime.fromisoformat(s["completed_at"]).timestamp()
            entry = {
                "batch_id": s["batch_id"],
                "size": s["total"],
                "completion_seconds": round(completed - created, 3),
            }
            cb = b.get("callback")
            if cb:
                entry["callback_delay_seconds"] = round(cb["received_at"] - completed, 3)
                entry["callback_attempts"] = b.get("callback_attempts_seen")
            batch_timing.append(entry)

    return {
        "run_id": run_id,
        "scenario": cfg["scenario"],
        "description": cfg["description"],
        "status": "aborted" if data["aborted"] else "finished",
        "note": MEASURED_NOTE,
        "started_at": datetime.fromtimestamp(start, UTC).isoformat(),
        "ended_at": datetime.fromtimestamp(end, UTC).isoformat(),
        "environment": machine_info(),
        "config": cfg,
        "totals": {
            **acc,
            "client_sent": data["client"]["sent_requests"],
            "http_status": data["client"]["http_status"],
            "http_errors": data["client"]["http_errors"],
            "drained": t["drained"],
            "load_seconds": round(load_end - start, 1),
            "total_seconds": round(duration, 1),
            "completion_rate_per_second_overall": round(acc["completed"] / duration, 1),
            "completion_rate_per_second_after_warmup": round(steady_completed / steady_secs, 1),
        },
        "latency_ms": {
            "end_to_end_all": data["latency"]["all"],
            "end_to_end_after_warmup": data["latency"]["steady"],
            "provider_call_after_warmup": data["latency"].get("provider_call"),
            "submit_ack": _pcts(data["client"]["ack_ms"]),
        },
        "models": C.model_summary(rows, data["referee"], start, warm, load_end),
        "referee": data["referee"],
        "limit_changes": data["limit_changes"],
        "batches": {
            "count": data["client"]["batches_submitted"],
            "sampled": len(batch_timing),
            "completion_seconds": _pcts([b["completion_seconds"] for b in batch_timing]),
            "callback_delay_seconds": _pcts(
                [b["callback_delay_seconds"] for b in batch_timing if "callback_delay_seconds" in b]
            ),
            "detail": batch_timing[:20],
        },
        "checks": results,
        "passed": all(r["passed"] for r in results),
        "timeseries": rows,
    }


# ---------- output ----------


def _chart(report: dict, path: Path) -> None:
    rows = report["timeseries"]
    models = [m for m in report["models"]]
    if not models:
        return
    t0 = rows[0]["ts"] if rows else 0
    fig, axes = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
    for model in models:
        s = C.series(rows, model)
        x = [r["ts"] - t0 for r in s]
        axes[0].plot(x, C.rolling([r["dispatched"] for r in s]), label=f"{model} observed (60 s)")
        axes[0].plot(x, [r["configured_rpm"] for r in s], "--", label=f"{model} limit")
        axes[1].plot(x, C.rolling([r["tokens"] for r in s]), label=f"{model} observed (60 s)")
        axes[1].plot(x, [r["configured_tpm"] for r in s], "--", label=f"{model} limit")
        axes[2].plot(x, [r["succeeded"] + r["failed"] for r in s], label=f"{model} completed/s")
    for change in report["limit_changes"]:
        for ax in axes:
            ax.axvline(change["ts"] - t0, color="grey", linewidth=0.8, linestyle=":")
    axes[0].set_ylabel("requests per 60 s")
    axes[1].set_ylabel("tokens per 60 s")
    axes[2].set_ylabel("completed / s")
    axes[2].set_xlabel("seconds since start")
    axes[0].set_title(f"{report['scenario']}: observed vs configured limits")
    for ax in axes:
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.1f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_fmt(c) for c in row) + " |" for row in rows]
    return out


def to_markdown(r: dict) -> str:
    env, tot, lat = r["environment"], r["totals"], r["latency_ms"]
    verdict = "PASS" if r["passed"] else "FAIL"
    lines = [
        f"# Run report — {r['scenario']} ({r['run_id']})",
        "",
        f"> {r['note']}",
        "",
        f"**Result: {verdict}** · status {r['status']} · {r['started_at']} → {r['ended_at']}",
        "",
        r["description"],
        "",
        "## Checks",
        "",
    ]
    check_rows = []
    for c in r["checks"]:
        m = c["measured"]
        measured = ", ".join(f"{k}={_fmt(v)}" for k, v in m.items()) if isinstance(m, dict) else m
        check_rows.append(
            [c["name"], "✅ PASS" if c["passed"] else "❌ FAIL", measured, c["expected"]]
        )
    lines += _table(["Check", "Result", "Measured", "Expected"], check_rows)

    total_keys = (
        "client_sent",
        "submitted",
        "completed",
        "succeeded",
        "failed",
        "expired",
        "rejected",
        "waiting",
        "completion_rate_per_second_overall",
        "completion_rate_per_second_after_warmup",
        "load_seconds",
        "total_seconds",
        "drained",
    )
    lines += ["", "## Totals", ""]
    lines += _table(["Metric", "Value"], [[k, tot[k]] for k in total_keys])

    lines += ["", "## Latency (ms)", ""]
    lat_rows = [
        [name, lat[name]["p50"], lat[name]["p95"], lat[name]["p99"], lat[name]["count"]]
        for name in ("end_to_end_all", "end_to_end_after_warmup", "provider_call_after_warmup")
        if lat.get(name)
    ]
    a = lat["submit_ack"]
    lat_rows.append(["submit_ack (HTTP)", a["p50"], a["p95"], a["p99"], ""])
    lines += _table(["Measure", "p50", "p95", "p99", "count"], lat_rows)
    lines += [
        "",
        "End-to-end = submit → final state, so it includes time waiting in the queue when demand "
        "is above capacity. Provider call = the final call to the provider only.",
    ]

    lines += ["", "## Per model", ""]
    model_rows = []
    for m, v in r["models"].items():
        limits = "; ".join(
            f"{x['rpm']:,} RPM / {x['tpm']:,} TPM" for x in v["configured_limits_seen"]
        )
        model_rows.append(
            [
                m,
                limits,
                v["observed_max_requests_in_60s"],
                v["observed_max_tokens_in_60s"],
                v["completed_per_minute_after_warmup"],
                v["provider_max_requests_in_any_60s"],
                v["provider_429s"],
            ]
        )
    lines += _table(
        [
            "Model",
            "Limits seen",
            "Max req / 60 s",
            "Max tokens / 60 s",
            "Completed / min after warm-up",
            "Provider max req in any 60 s",
            "Provider 429s",
        ],
        model_rows,
    )

    if r["limit_changes"]:
        lines += ["", "## Limit changes", ""]
        t0 = math.floor(datetime.fromisoformat(r["started_at"]).timestamp())
        for c in r["limit_changes"]:
            lines.append(
                f"- t+{c['ts'] - t0:.0f}s: {c['model']} → {c['rpm']:,} RPM / {c['tpm']:,} TPM"
            )

    b = r["batches"]
    if b["count"]:
        comp, cbd = b["completion_seconds"], b["callback_delay_seconds"]
        lines += [
            "",
            "## Batches",
            "",
            f"- batches submitted: {b['count']:,} (timing sampled for {b['sampled']})",
            f"- completion time (s): p50 {_fmt(comp['p50'])}, p95 {_fmt(comp['p95'])},"
            f" p99 {_fmt(comp['p99'])}",
        ]
        if cbd["p50"] is not None:
            lines.append(
                f"- callback delay after completion (s): p50 {_fmt(cbd['p50'])},"
                f" p99 {_fmt(cbd['p99'])}"
            )
    lines += [
        "",
        "## Environment",
        "",
        f"- {env['cpu']}, {env['cpu_count']} cores, {env['ram_gb']} GB RAM",
        f"- {env['os']}, Python {env['python']}",
        "",
        "![throughput](throughput.png)",
        "",
    ]
    return "\n".join(lines)


def write_report(report: dict, results_dir: str) -> Path:
    stamp = datetime.fromisoformat(report["started_at"]).strftime("%Y%m%d-%H%M%S")
    out = Path(results_dir) / report["scenario"] / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "report.md").write_text(to_markdown(report))
    _chart(report, out / "throughput.png")
    return out
