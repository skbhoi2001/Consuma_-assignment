"""End-to-end smoke test of an async batch against running services (Scenario 3 shape).

Start simulator, receiver (RECEIVER_FAIL_FIRST_N=2) and gateway first, then:
    python scripts/smoke_batch.py --n 10000
"""

import argparse
import time

import httpx

CHECK_FIELDS = ("status", "total", "succeeded", "failed", "expired", "rejected", "results_url")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="http://localhost:8000")
    ap.add_argument("--simulator", default="http://localhost:9000")
    ap.add_argument("--receiver", default="http://localhost:9100")
    ap.add_argument("--n", type=int, default=10_000)
    ap.add_argument("--transient", type=float, default=0.10)
    ap.add_argument("--permanent", type=float, default=0.02)
    a = ap.parse_args()

    for model in ("model-a", "model-b"):
        httpx.put(
            f"{a.simulator}/sim/models/{model}",
            json={"transient_error_rate": a.transient, "permanent_error_rate": a.permanent},
        ).raise_for_status()

    requests = [
        {
            "model": "model-a" if i % 2 else "model-b",
            "estimated_tokens": 1000,
            "request_id": f"c-{i}",
        }
        for i in range(a.n)
    ]
    t0 = time.perf_counter()
    r = httpx.post(
        f"{a.gateway}/v1/batches",
        json={"requests": requests, "callback_url": f"{a.receiver}/callbacks/receive"},
        timeout=60,
    )
    r.raise_for_status()
    ack_ms = (time.perf_counter() - t0) * 1000
    batch_id = r.json()["batch_id"]
    print(f"ack: HTTP {r.status_code} in {ack_ms:.0f} ms  batch={batch_id}")

    slowest = 0.0
    while True:
        t = time.perf_counter()
        status = httpx.get(f"{a.gateway}/v1/batches/{batch_id}", timeout=30).json()
        slowest = max(slowest, time.perf_counter() - t)
        if status["callback_status"] in ("delivered", "dead_lettered"):
            break
        time.sleep(0.5)
    print(
        f"finished, callback {status['callback_status']} after {time.perf_counter() - t0:.1f} s"
        f" (slowest status call {slowest * 1000:.0f} ms)"
    )
    print({k: status[k] for k in ("status", "total", "succeeded", "failed", "expired", "pending")})

    got = httpx.get(f"{a.receiver}/callbacks/received").json()
    payload = got["received"][-1]["payload"]
    attempts = got["attempts"][batch_id]
    print(f"receiver attempts: {attempts}, accepted attempt {payload['delivery_attempt']}")
    print(
        "callback summary matches GET status:", all(payload[k] == status[k] for k in CHECK_FIELDS)
    )

    ids: list[str] = []
    cursor = None
    while True:
        params = {"limit": 5000, **({"cursor": cursor} if cursor else {})}
        page = httpx.get(f"{a.gateway}/v1/batches/{batch_id}/results", params=params).json()
        ids += [item["request_id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    print(f"result rows: {len(ids)}, unique ids: {len(set(ids))}")
    print("accounting:", httpx.get(f"{a.gateway}/v1/stats").json()["accounting"])


if __name__ == "__main__":
    main()
