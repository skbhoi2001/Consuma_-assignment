import json

from loadgen.report import build_report, to_markdown, write_report


def fake_run():
    start = 1_000.0
    rows = [
        {
            "ts": 1000 + i,
            "model": "m",
            "configured_rpm": 600,
            "configured_tpm": 10**6,
            "dispatched": 10,
            "tokens": 100,
            "succeeded": 10,
            "failed": 0,
            "expired": 0,
            "rejected": 0,
            "retried": 0,
            "provider_429": 0,
        }
        for i in range(40)
    ]
    lat = {"count": 400, "sampled": 400, "p50": 5.0, "p95": 9.0, "p99": 12.0}
    acc0 = dict(submitted=0, succeeded=0, failed=0, expired=0, rejected=0, waiting=0)
    acc1 = dict(submitted=400, succeeded=400, failed=0, expired=0, rejected=0, waiting=0)
    return {
        "config": {
            "scenario": "t",
            "description": "test run",
            "warmup_seconds": 5,
            "models": {"m": 1.0},
            "checks": ["accounting", "limits_respected"],
            "callback_fail_first_n": 0,
            "limit_changes": [],
            "set_limits": {},
        },
        "times": {"start": start, "load_end": start + 40, "end": start + 41, "drained": True},
        "aborted": False,
        "client": {
            "sent_requests": 400,
            "http_status": {"202": 40},
            "http_errors": 0,
            "http_error_requests": 0,
            "ack_ms": [3.0, 4.0],
            "batches_submitted": 0,
        },
        "health": {"before": {"pid": 1, "started_at": 1}, "after": {"pid": 1, "started_at": 1}},
        "accounting": {"before": acc0, "after": acc1},
        "timeseries": rows,
        "limit_changes": [],
        "latency": {"all": lat, "steady": lat},
        "referee": {
            "m": {
                "rate_limited": 0,
                "violations": 0,
                "max_requests_in_any_60s": 400,
                "max_tokens_in_any_60s": 4000,
            }
        },
        "batches": [],
    }


def test_build_and_write_report(tmp_path):
    report = build_report(fake_run(), "t-1")
    assert report["passed"]
    assert report["totals"]["completed"] == 400
    assert report["totals"]["completion_rate_per_second_after_warmup"] == 10.0
    md = to_markdown(report)
    assert "**Result: PASS**" in md and "| accounting | ✅ PASS |" in md

    out = write_report(report, str(tmp_path))
    assert (out / "report.md").exists() and (out / "throughput.png").exists()
    assert json.loads((out / "report.json").read_text())["run_id"] == "t-1"
