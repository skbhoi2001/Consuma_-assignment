"""Load and validate model configuration from YAML."""

from pathlib import Path

import yaml

from backend.common.schemas import ModelConfig


class ModelConfigError(ValueError):
    pass


def load_models(path: Path | str) -> dict[str, ModelConfig]:
    path = Path(path)
    if not path.exists():
        raise ModelConfigError(f"models config not found: {path}")

    data = yaml.safe_load(path.read_text()) or {}
    raw_models = data.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise ModelConfigError("models.yaml must contain a non-empty 'models' list")

    models: dict[str, ModelConfig] = {}
    for raw in raw_models:
        cfg = ModelConfig(**raw)
        if cfg.name in models:
            raise ModelConfigError(f"duplicate model name: {cfg.name}")
        models[cfg.name] = cfg
    return models
