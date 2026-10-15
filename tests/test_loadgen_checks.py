"""The PASS/FAIL logic used by every scenario report."""

from loadgen import checks as C


def rows(model, dispatched, rpm=100, tpm=10_000, tokens_each=10, start=1000, completed=None):
    out = []
    for i, d in enumerate(dispatched):
        done = completed[i] if completed else d
        out.append(
            {
                "ts": start + i,
                "model": model,
                "configured_rpm": rpm if not callable(rpm) else rpm(i),
                "configured_tpm": tpm,
                "dispatched": d,
                "tokens": d * tokens_each,
                "succeeded": done,
                "failed": 0,
                "expired": 0,
                "rejected": 0,
                "retried": 0,
                "provider_429": 0,
            }
        )
    return out


def test_rolling():
    assert C.rolling([1, 1, 1, 1], window=2) == [1, 2, 2, 2]


def test_steady_window():
    assert C.steady_window(0, 60, 360, 500) == (60, 360)  # long load: measure during load
    assert C.steady_window(0, 0, 1, 30) == (0, 30)  # one batch: measure until drained


def test_window_usage_ok_and_violation():
    ok = rows("m", [1] * 120, rpm=61)  # 61 per 61 buckets -> at the limit, fine
    assert C.window_usage(ok, "m")["violating_seconds"] == []
    bad = rows("m", [2] * 120, rpm=61)
    assert C.window_usage(bad, "m")["violating_seconds"]


def test_sending_nothing_after_a_cut_is_not_a_violation():
    # 100 req/s for 10 s, then the limit drops to 50 and nothing is sent for a minute
    data = [100] * 10 + [0] * 61 + [1]
    r = rows("m", data, rpm=lambda i: 1000 if i < 10 else 50)
    assert C.window_usage(r, "m")["violating_seconds"] == []


def test_second_of_a_limit_change_uses_the_higher_limit():
    # 50 req/s for 2 s, then the limit drops from 1000 to 60 during second 2 (after 50 were sent)
    r = rows("m", [50, 50, 50, 0], rpm=lambda i: 1000 if i < 2 else 60)
    assert C.window_usage(r, "m")["violating_seconds"] == []
    late = rows("m", [50, 50, 50, 10], rpm=lambda i: 1000 if i < 2 else 60)
    assert C.window_usage(late, "m")["violating_seconds"]  # sending at t=3 breaks the new limit


def test_check_limits_uses_referee():
    r = rows("m", [1] * 10)
    good = C.check_limits(r, {"m": {"rate_limited": 0, "violations": 0}}, ["m"])[0]
    bad = C.check_limits(r, {"m": {"rate_limited": 3, "violations": 0}}, ["m"])[0]
    assert good["passed"] and not bad["passed"]


def test_accounting():
    before = dict(submitted=10, succeeded=5, failed=1, expired=0, rejected=4, waiting=0)
    after = dict(submitted=110, succeeded=95, failed=6, expired=3, rejected=6, waiting=0)
    d = C.delta(before, after)
    assert d == {
        "submitted": 100,
        "succeeded": 90,
        "failed": 5,
        "expired": 3,
        "rejected": 2,
        "completed": 95,
        "waiting": 0,
    }
    assert C.check_accounting(d, sent=100, http_error_requests=0)["passed"]
    assert not C.check_accounting(d, sent=105, http_error_requests=0)["passed"]


def test_utilization():
    r = rows("m", [1] * 120, rpm=60)  # 60 completed per minute = 100% of 60 RPM
    assert C.check_utilization(r, "m", 1000, 1120, 60)["passed"]
    assert not C.check_utilization(r, "m", 1000, 1120, 100)["passed"]  # 60%


def test_scenario2():
    a = [10] * 100 + [1] * 200 + [10] * 200  # cut at t=100, restore at t=300
    b = [5] * 500
    data = rows("a", a, rpm=1000) + rows("b", b, rpm=300)
    changes = [{"ts": 1100, "model": "a", "rpm": 60}, {"ts": 1300, "model": "a", "rpm": 600}]
    results = {c["name"]: c["passed"] for c in C.check_scenario2(data, changes, "a", "b", 300)}
    assert results == {"adjusts_down[a]": True, "recovers[a]": True, "keeps_processing[b]": True}


def test_no_restart():
    same = {"before": {"pid": 1, "started_at": 5.0}, "after": {"pid": 1, "started_at": 5.0}}
    other = {"before": {"pid": 1, "started_at": 5.0}, "after": {"pid": 2, "started_at": 9.0}}
    assert C.check_no_restart(same)["passed"] and not C.check_no_restart(other)["passed"]


def test_batches():
    summary = {
        "batch_id": "b",
        "status": "completed",
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "expired": 0,
        "rejected": 0,
        "pending": 0,
        "results_url": "u",
        "completed_at": "2026-01-01T00:00:10+00:00",
        "callback_status": "delivered",
    }
    payload = {
        k: summary[k]
        for k in (
            "batch_id",
            "status",
            "total",
            "succeeded",
            "failed",
            "expired",
            "rejected",
            "results_url",
        )
    }
    batch = {
        "summary": summary,
        "callback": {"payload": payload, "received_at": 11.0},
        "callback_attempts_seen": 3,
        "result_ids": ["r1", "r2"],
        "result_finished_max": "2026-01-01T00:00:09+00:00",
    }
    results = {c["name"]: c["passed"] for c in C.check_batches([batch], 2, [120.0])}
    assert all(results.values()), results
    dup = {**batch, "result_ids": ["r1", "r1"]}
    assert not {c["name"]: c["passed"] for c in C.check_batches([dup], 2, [1.0])}[
        "request_ids_exactly_once"
    ]
