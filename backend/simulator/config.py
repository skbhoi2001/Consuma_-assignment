"""Simulator model configuration."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.common.schemas import ModelName


class SimModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ModelName
    rpm: int = Field(gt=0)
    tpm: int = Field(gt=0)
    latency_median_ms: float = Field(200, ge=0)
    latency_sigma: float = Field(0.5, ge=0)
    transient_error_rate: float = Field(0.0, ge=0, le=1)
    permanent_error_rate: float = Field(0.0, ge=0, le=1)
    enforce_limits: bool = True

    @model_validator(mode="after")
    def rates_sum_to_at_most_one(self) -> "SimModelConfig":
        if self.transient_error_rate + self.permanent_error_rate > 1:
            raise ValueError("transient_error_rate + permanent_error_rate must be <= 1")
        return self


class SimModelUpdate(BaseModel):
    """Body of PUT /sim/models/{name}: any subset of fields."""

    model_config = ConfigDict(extra="forbid")

    rpm: int | None = Field(None, gt=0)
    tpm: int | None = Field(None, gt=0)
    latency_median_ms: float | None = Field(None, ge=0)
    latency_sigma: float | None = Field(None, ge=0)
    transient_error_rate: float | None = Field(None, ge=0, le=1)
    permanent_error_rate: float | None = Field(None, ge=0, le=1)
    enforce_limits: bool | None = None

    @model_validator(mode="after")
    def at_least_one(self) -> "SimModelUpdate":
        if not self.model_fields_set:
            raise ValueError("provide at least one field")
        return self


class SimConfigError(ValueError):
    pass


def load_sim_config(path: Path | str) -> list[SimModelConfig]:
    path = Path(path)
    if not path.exists():
        raise SimConfigError(f"simulator config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    defaults = data.get("defaults") or {}
    raw_models = data.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise SimConfigError("simulator.yaml must contain a non-empty 'models' list")

    models = [SimModelConfig(**{**defaults, **raw}) for raw in raw_models]
    names = [m.name for m in models]
    if len(names) != len(set(names)):
        raise SimConfigError("duplicate model name in simulator.yaml")
    return models
