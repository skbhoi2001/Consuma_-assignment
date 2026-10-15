import pytest

from backend.common.schemas import ModelConfig
from backend.common.states import RequestState as S
from backend.metrics.collector import MetricsCollector, percentile


def collector(**kw):
    models = {"a": ModelConfig(name="a", rpm=100, tpm=10_000)}
    return MetricsCollector(models, clock=lambda: 100.0, **kw)


def test_percentile_nearest_rank():
    values = sorted(float(i) for i in range(1, 101))
    assert percentile(values, 50) == 50 and percentile(values, 99) == 99
    assert percentile([], 50) is None


def test_dispatch_and_final_counted_per_second():
    m = collector()
    for _ in range(3):
        m.on_dispatch("a", 10, now=10.1)
    m.on_final("a", S.SUCCEEDED, 12.0, now=10.5)
    m.on_final("a", S.FAILED, 20.0, now=11.2)
    m.on_final("a", S.EXPIRED, None, now=11.3)
    rows = m.timeseries(since=10, until=11)
    assert [(r["ts"], r["dispatched"], r["tokens"]) for r in rows] == [(10, 3, 30), (11, 0, 0)]
    assert (rows[0]["succeeded"], rows[1]["failed"], rows[1]["expired"]) == (1, 1, 1)


def test_gaps_are_filled_and_carry_the_limit():
    m = collector()
    m.on_dispatch("a", 1, now=10.0)
    m.on_limits("a", 50, 5_000, now=12.0)
    rows = m.timeseries(since=10, until=14)
    assert [r["configured_rpm"] for r in rows] == [100, 100, 50, 50, 50]
    assert m.limit_changes[0]["old"] == (100, 10_000)


def test_latency_percentiles_only_for_completed():
    m = collector()
    for i in range(1, 101):
        m.on_final("a", S.SUCCEEDED, float(i), now=10.0)
    m.on_final("a", S.EXPIRED, 9999.0, now=10.0)  # not a provider outcome
    lat = m.latency()
    assert lat["count"] == 100 and lat["p50"] == 50 and lat["p99"] == 99


def test_latency_reservoir_is_bounded():
    m = collector(latency_samples_per_second=50)
    for i in range(1_000):
        m.on_final("a", S.SUCCEEDED, float(i), now=10.0)
    lat = m.latency()
    assert lat["count"] == 1_000 and lat["sampled"] == 50


def test_live_rolling_window():
    m = collector()
    for second in range(40, 101):
        m.on_dispatch("a", 2, now=second + 0.5)
    live = m.live(now=100.5)["a"]
    assert live["rolling_60s_requests"] == 60  # seconds 41..100
    assert live["rolling_60s_tokens"] == 120
    assert live["dispatched_last_second"] == 1


def test_unknown_model_ignored():
    m = collector()
    m.on_dispatch("zzz", 1, now=1.0)
    assert m.timeseries(model="zzz") == []


def test_prometheus_export():
    from prometheus_client import generate_latest

    m = collector()
    m.on_dispatch("a", 7, now=1.0)
    text = generate_latest(m.registry).decode()
    assert 'gateway_dispatched_requests_total{model="a"} 1.0' in text
    assert 'gateway_configured_rpm{model="a"} 100.0' in text


@pytest.mark.parametrize("state", [S.SUCCEEDED, S.FAILED, S.EXPIRED, S.REJECTED])
def test_every_final_state_has_a_column(state):
    m = collector()
    m.on_final("a", state, 1.0, now=5.0)
    assert m.timeseries(since=5, until=5)[0][state.value] == 1


def test_provider_call_latency_tracked_separately():
    m = collector()
    m.on_final("a", S.SUCCEEDED, 5_000.0, now=10.0, call_ms=200.0)  # waited ~5 s in the queue
    m.on_final("a", S.FAILED, 7_000.0, now=10.0, call_ms=300.0)
    assert m.latency()["p50"] == 5_000.0
    call = m.latency(kind="provider_call")
    assert call["count"] == 2 and call["p50"] == 200.0 and call["p99"] == 300.0
    assert m.live(now=10.5)["a"]["provider_call_p50_ms"] == 200.0
