import pytest

from backend.simulator.config import (
    SimConfigError,
    SimModelConfig,
    SimModelUpdate,
    load_sim_config,
)
from backend.simulator.provider import ProviderSimulator
from backend.simulator.referee import ExactWindow


def model(**kw):
    base = {"name": "m", "rpm": 5, "tpm": 10_000, "latency_median_ms": 0}
    base.update(kw)
    return SimModelConfig(**base)


def test_exact_window_is_half_open():
    w = ExactWindow()
    w.add(0.0, 10)
    assert w.current(59.999) == (1, 10)
    assert w.current(60.0) == (0, 0)  # window is (t-60, t]
    assert w.max_requests == 1


def test_success_and_actual_tokens():
    sim = ProviderSimulator([model()], seed=1)
    out = sim.handle("m", 1000, now=1.0)
    assert out.status_code == 200 and out.kind == "success"
    assert 900 <= out.actual_tokens <= 1100


def test_unknown_model():
    assert ProviderSimulator([model()]).handle("nope", 10, now=1.0).status_code == 404


def test_enforced_limit_returns_429_with_retry_after():
    sim = ProviderSimulator([model(rpm=5)], seed=1)
    codes = [sim.handle("m", 10, now=1.0).status_code for _ in range(7)]
    assert codes == [200] * 5 + [429] * 2
    out = sim.handle("m", 10, now=30.0)
    assert out.status_code == 429 and out.retry_after == pytest.approx(31.0)
    assert sim.handle("m", 10, now=61.5).status_code == 200
    s = sim.stats(now=61.5)["m"]
    assert s["rate_limited"] == 3 and s["max_requests_in_any_60s"] == 5 and s["violations"] == 0


def test_observe_mode_counts_violations_instead_of_429():
    sim = ProviderSimulator([model(rpm=5, enforce_limits=False)], seed=1)
    codes = [sim.handle("m", 10, now=1.0).status_code for _ in range(7)]
    assert codes == [200] * 7
    s = sim.stats(now=1.0)["m"]
    assert s["violations"] == 2 and s["max_requests_in_any_60s"] == 7


def test_tpm_enforced():
    sim = ProviderSimulator([model(rpm=100, tpm=1_000)], seed=1)
    assert sim.handle("m", 600, now=1.0).status_code == 200
    assert sim.handle("m", 600, now=1.0).status_code == 429


def test_error_rates():
    always_permanent = ProviderSimulator([model(rpm=100, permanent_error_rate=1.0)], seed=1)
    assert always_permanent.handle("m", 10, now=1.0).status_code == 400
    always_transient = ProviderSimulator([model(rpm=100, transient_error_rate=1.0)], seed=1)
    assert always_transient.handle("m", 10, now=1.0).status_code == 503


def test_error_rate_mix_is_roughly_right():
    sim = ProviderSimulator(
        [model(rpm=10**6, tpm=10**9, transient_error_rate=0.2, permanent_error_rate=0.1)], seed=7
    )
    for i in range(10_000):
        sim.handle("m", 10, now=1.0 + i * 1e-4)
    s = sim.stats(now=2.0)["m"]
    assert s["transient_errors"] / 10_000 == pytest.approx(0.2, abs=0.02)
    assert s["permanent_errors"] / 10_000 == pytest.approx(0.1, abs=0.02)


def test_latency_has_requested_median():
    sim = ProviderSimulator([model(rpm=10**6, latency_median_ms=200, latency_sigma=0.5)], seed=3)
    lat = sorted(sim.handle("m", 1, now=1.0).latency_ms for _ in range(2_001))
    assert lat[1000] == pytest.approx(200, rel=0.1)


def test_runtime_update_keeps_window():
    sim = ProviderSimulator([model(rpm=10)], seed=1)
    for _ in range(8):
        sim.handle("m", 1, now=1.0)
    sim.update_model("m", SimModelUpdate(rpm=5))
    assert sim.handle("m", 1, now=2.0).status_code == 429  # 8 already in window > 5
    assert sim.models["m"].rpm == 5


def test_invalid_config():
    with pytest.raises(ValueError):
        model(transient_error_rate=0.7, permanent_error_rate=0.5)
    with pytest.raises(ValueError):
        SimModelUpdate()


def test_load_sim_config_applies_defaults(tmp_path):
    f = tmp_path / "sim.yaml"
    f.write_text(
        "defaults: {latency_median_ms: 50, transient_error_rate: 0.1}\n"
        "models:\n"
        "  - {name: a, rpm: 10, tpm: 100}\n"
        "  - {name: b, rpm: 10, tpm: 100, transient_error_rate: 0.3}\n"
    )
    a, b = load_sim_config(f)
    assert a.latency_median_ms == 50 and a.transient_error_rate == 0.1
    assert b.transient_error_rate == 0.3


def test_repo_sim_config_loads():
    names = [m.name for m in load_sim_config("config/simulator.yaml")]
    assert names == ["model-a", "model-b", "model-capacity"]


def test_duplicate_names_rejected(tmp_path):
    f = tmp_path / "sim.yaml"
    f.write_text("models:\n  - {name: a, rpm: 1, tpm: 1}\n  - {name: a, rpm: 1, tpm: 1}\n")
    with pytest.raises(SimConfigError):
        load_sim_config(f)
