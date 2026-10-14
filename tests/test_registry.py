import pytest

from backend.common.model_registry import ModelConfigError, load_models
from backend.common.settings import Settings


def test_default_config_loads():
    models = load_models("config/models.yaml")
    assert models["model-a"].rpm == 30_000
    assert models["model-b"].tpm == 40_000_000
    assert models["model-capacity"].rpm == 50_000


def test_duplicate_model_rejected(tmp_path):
    f = tmp_path / "m.yaml"
    f.write_text("models:\n  - {name: a, rpm: 1, tpm: 1}\n  - {name: a, rpm: 2, tpm: 2}\n")
    with pytest.raises(ModelConfigError):
        load_models(f)


def test_missing_file(tmp_path):
    with pytest.raises(ModelConfigError):
        load_models(tmp_path / "nope.yaml")


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("TARGET_UTILIZATION", "0.9")
    monkeypatch.setenv("MAX_ATTEMPTS", "6")
    s = Settings()
    assert s.target_utilization == 0.9
    assert s.max_attempts == 6
