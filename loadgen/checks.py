"""PASS/FAIL checks over a collected run. Pure functions -> easy to unit-test.

Each check returns {"name", "passed", "measured", "expected"}.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

WINDOW = 60


def steady_window(start: float, warmup: float, load_end: float, end: float) -> tuple[float, float]:
    """Measurement window after warm-up. For short bursts (e.g. one batch) the work happens
    after submission ends, so measure until everything drained instead."""
    warm = start + warmup
    return (warm, load_end) if load_end - warm >= 30 else (warm, end)


def series(rows: list[dict], model: str) -> list[dict]:
    return [r for r in rows if r["model"] == model]


def rolling(values: list[int], window: int = WINDOW) -> list[int]:
    """out[i] = sum(values[i-window+1 .. i])."""
    out, acc = [], 0
    for i, v in enumerate(values):
        acc += v
        if i >= window:
            acc -= values[i - window]
        out.append(acc)
    return out


def window_usage(rows: list[dict], model: str) -> dict[str, Any]:
    """Gateway-side view: requests/tokens sent in every 60 s window vs the limit in force.

    The limiter counts 61 one-second buckets, so for every second in which we sent anything,
    the 61-second trailing sum must be within that second's configured limit. That guarantees
    no real 60-second period exceeded it."""
    s = series(rows, model)
    req = [r["dispatched"] for r in s]
    tok = [r["tokens"] for r in s]
    req61, tok61 = rolling(req, WINDOW + 1), rolling(tok, WINDOW + 1)
    req60, tok60 = rolling(req), rolling(tok)

    def limit(i: int, key: str) -> int:
        # In the second a limit changes, requests may have been sent just before the change;
        # per-second data can't tell, so that second is held to the higher of the two limits.
        return max(s[i][key], s[i - 1][key]) if i else s[i][key]

    violations = [
        s[i]["ts"]
        for i in range(len(s))
        if req[i] > 0
        and (req61[i] > limit(i, "configured_rpm") or tok61[i] > limit(i, "configured_tpm"))
    ]
    return {
        "max_requests_60s": max(req60, default=0),
        "max_tokens_60s": max(tok60, default=0),
        "max_rpm_utilization": max(
            (req60[i] / s[i]["configured_rpm"] for i in range(len(s))), default=0.0
        ),
        "violating_seconds": violations,
    }


def check_limits(rows: list[dict], referee: dict, models: list[str]) -> list[dict]:
    out = []
    for model in models:
        usage = window_usage(rows, model)
        ref = referee.get(model, {})
        passed = (
            not usage["violating_seconds"]
            and ref.get("rate_limited", 0) == 0
            and ref.get("violations", 0) == 0
        )
        out.append(
            {
                "name": f"limits_respected[{model}]",
                "passed": passed,
                "measured": {
                    "gateway_max_requests_in_60s": usage["max_requests_60s"],
                    "gateway_max_tokens_in_60s": usage["max_tokens_60s"],
                    "gateway_violating_seconds": len(usage["violating_seconds"]),
                    "provider_max_requests_in_any_60s": ref.get("max_requests_in_any_60s"),
                    "provider_max_tokens_in_any_60s": ref.get("max_tokens_in_any_60s"),
                    "provider_429s": ref.get("rate_limited"),
                    "provider_violations": ref.get("violations"),
                },
                "expected": "no 60 s window above the limit in force; 0 provider 429s",
            }
        )
    return out


def delta(before: dict, after: dict) -> dict[str, int]:
    keys = ("submitted", "succeeded", "failed", "expired", "rejected")
    d = {k: after[k] - before[k] for k in keys}
    d["completed"] = d["succeeded"] + d["failed"]
    d["waiting"] = after["waiting"]
    return d


def check_accounting(acc: dict[str, int], sent: int, http_error_requests: int) -> dict:
    balanced = (
        acc["submitted"] == acc["completed"] + acc["waiting"] + acc["expired"] + acc["rejected"]
    )
    all_seen = acc["submitted"] + http_error_requests == sent
    return {
        "name": "accounting",
        "passed": balanced and all_seen,
        "measured": {**acc, "client_sent": sent, "http_error_requests": http_error_requests},
        "expected": "submitted == completed + waiting + expired + rejected, and == client sent",
    }


def completed_per_minute(rows: list[dict], model: str, start: float, end: float) -> float:
    s = [r for r in series(rows, model) if start <= r["ts"] < end]
    seconds = max(1, len(s))
    return sum(r["succeeded"] + r["failed"] for r in s) * 60 / seconds


def check_utilization(rows: list[dict], model: str, start: float, end: float, rpm: int) -> dict:
    per_min = completed_per_minute(rows, model, start, end)
    return {
        "name": f"utilization[{model}]",
        "passed": per_min >= 0.9 * rpm,
        "measured": {
            "completed_per_minute": round(per_min),
            "share_of_limit": round(per_min / rpm, 4),
        },
        "expected": f">= 90% of {rpm} RPM after warm-up",
    }


def rolling_at(rows: list[dict], model: str, ts: float) -> int:
    """Requests dispatched in the 60 s ending at second `ts`."""
    return sum(r["dispatched"] for r in series(rows, model) if ts - WINDOW < r["ts"] <= ts)


def check_scenario2(
    rows: list[dict], changes: list[dict], constrained: str, other: str, other_rpm: int
) -> list[dict]:
    """A is cut and later restored; B must keep going."""
    mine = sorted((c for c in changes if c["model"] == constrained), key=lambda c: c["ts"])
    if len(mine) < 2:
        return [
            {
                "name": "scenario2",
                "passed": False,
                "measured": "limit changes missing",
                "expected": "a decrease and a restore",
            }
        ]
    cut, restore = mine[0], mine[1]
    t_cut, t_restore = int(cut["ts"]), int(restore["ts"])
    low, high = cut["rpm"], restore["rpm"]

    settled = completed_per_minute(rows, constrained, t_cut + 75, t_restore)
    recovered = rolling_at(rows, constrained, t_restore + 75)
    b_during = completed_per_minute(rows, other, t_cut, t_restore)
    return [
        {
            "name": f"adjusts_down[{constrained}]",
            "passed": settled <= low and settled >= 0.8 * low,
            "measured": {"completed_per_minute_while_cut": round(settled)},
            "expected": f"settles at <= {low} RPM (and keeps working, >= 80%)",
        },
        {
            "name": f"recovers[{constrained}]",
            "passed": recovered >= 0.8 * high,
            "measured": {"requests_in_60s_ending_75s_after_restore": recovered},
            "expected": f">= 80% of {high} within 75 s of restore",
        },
        {
            "name": f"keeps_processing[{other}]",
            "passed": b_during >= 0.9 * other_rpm,
            "measured": {"completed_per_minute_while_other_cut": round(b_during)},
            "expected": f">= 90% of {other_rpm} RPM while {constrained} is constrained",
        },
    ]


def check_no_restart(health: dict) -> dict:
    same = (
        health["before"]["pid"] == health["after"]["pid"]
        and health["before"]["started_at"] == health["after"]["started_at"]
    )
    return {
        "name": "no_restart",
        "passed": same,
        "measured": {"pid": [health["before"]["pid"], health["after"]["pid"]]},
        "expected": "same gateway process before and after",
    }


def check_batches(batches: list[dict], fail_first_n: int, ack_ms: list[float]) -> list[dict]:
    fields = ("status", "total", "succeeded", "failed", "expired", "rejected", "results_url")
    ack_ok = bool(ack_ms) and max(ack_ms) < 1000
    after_final, retried, matches, exactly_once = [], [], [], []
    for b in batches:
        s, cb = b["summary"], b.get("callback")
        if cb is None:
            after_final.append(False)
            retried.append(False)
            matches.append(False)
        else:
            p = cb["payload"]
            done = sum(p[k] for k in ("succeeded", "failed", "expired", "rejected")) == p["total"]
            last_result = b.get("result_finished_max")
            in_time = s["completed_at"] is not None and (
                last_result is None
                or datetime.fromisoformat(last_result) <= datetime.fromisoformat(s["completed_at"])
            )
            after_final.append(done and s["pending"] == 0 and in_time)
            retried.append(
                b.get("callback_attempts_seen") == fail_first_n + 1
                and s["callback_status"] == "delivered"
            )
            matches.append(all(p[k] == s[k] for k in fields))
        if "result_ids" in b:
            ids = b["result_ids"]
            exactly_once.append(len(ids) == len(set(ids)) == s["total"])
    out = [
        {
            "name": "ack_under_1s",
            "passed": ack_ok,
            "measured": {"max_ack_ms": round(max(ack_ms), 1) if ack_ms else None},
            "expected": "< 1000 ms",
        },
        {
            "name": "callback_after_all_final",
            "passed": bool(after_final) and all(after_final),
            "measured": {"batches": len(after_final)},
            "expected": "every request final first",
        },
        {
            "name": "callback_delivered_after_failures",
            "passed": bool(retried) and all(retried),
            "measured": {
                "attempts_min": min(
                    (b.get("callback_attempts_seen") or 0 for b in batches), default=0
                ),
                "attempts_max": max(
                    (b.get("callback_attempts_seen") or 0 for b in batches), default=0
                ),
                "delivered": sum(retried),
            },
            "expected": f"{fail_first_n} rejections, then delivered",
        },
        {
            "name": "callback_matches_status",
            "passed": bool(matches) and all(matches),
            "measured": {"batches": len(matches)},
            "expected": "callback summary == GET /v1/batches",
        },
    ]
    if exactly_once:
        out.append(
            {
                "name": "request_ids_exactly_once",
                "passed": all(exactly_once),
                "measured": {"batches": len(exactly_once)},
                "expected": "every id once in the final results",
            }
        )
    return out


def model_summary(rows: list[dict], referee: dict, start: float, warm: float, end: float) -> dict:
    out: dict[str, dict] = defaultdict(dict)
    for model in sorted({r["model"] for r in rows}):
        s = series(rows, model)
        if not any(r["dispatched"] for r in s):
            continue
        usage = window_usage(rows, model)
        configured = sorted({(r["configured_rpm"], r["configured_tpm"]) for r in s})
        out[model] = {
            "configured_limits_seen": [{"rpm": a, "tpm": b} for a, b in configured],
            "observed_max_requests_in_60s": usage["max_requests_60s"],
            "observed_max_tokens_in_60s": usage["max_tokens_60s"],
            "completed_per_minute_after_warmup": round(
                completed_per_minute(rows, model, warm, end)
            ),
            "dispatched_total": sum(r["dispatched"] for r in s),
            "provider_max_requests_in_any_60s": referee.get(model, {}).get(
                "max_requests_in_any_60s"
            ),
            "provider_429s": referee.get(model, {}).get("rate_limited"),
        }
    return dict(out)
