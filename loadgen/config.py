"""Load-test configuration (also the format of scenarios/*.yaml)."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class LimitSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rpm: int | None = Field(None, gt=0)
    tpm: int | None = Field(None, gt=0)


class LimitChange(LimitSet):
    at_seconds: float = Field(ge=0)  # seconds after load starts
    model: str


class LoadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: str = "custom"
    description: str = ""
    gateway_url: str = "http://localhost:8000"
    simulator_url: str = "http://localhost:9000"

    # Traffic shape
    rate: float = Field(gt=0, description="requests per second submitted (open loop)")
    duration_seconds: float = Field(gt=0)
    models: dict[str, float] = Field(min_length=1, description="model -> traffic weight")
    batch_size: int = Field(1, ge=1, le=100_000)
    tokens: int = Field(1000, gt=0)
    token_jitter: float = Field(0.0, ge=0, lt=1, description="+/- fraction of tokens")

    # Measurement
    warmup_seconds: float = Field(60, ge=0)
    drain_timeout_seconds: float = Field(180, gt=0)
    max_concurrent_posts: int = Field(64, gt=0)
    batch_summaries_max: int = Field(200, ge=0, description="batches to fetch timing for")
    verify_results: bool = False  # page through every batch's results (exactly-once check)

    # Callbacks (embedded receiver)
    callback: bool = False
    callback_host: str = "127.0.0.1"
    callback_port: int = 9200
    callback_fail_first_n: int = Field(0, ge=0)

    # Environment setup
    set_limits: dict[str, LimitSet] = Field(default_factory=dict)
    simulator: dict[str, dict[str, Any]] = Field(default_factory=dict)
    limit_changes: list[LimitChange] = Field(default_factory=list)

    checks: list[str] = Field(default_factory=lambda: ["accounting", "limits_respected"])
    results_dir: str = "results"

    @model_validator(mode="after")
    def weights_positive(self) -> "LoadConfig":
        if any(w <= 0 for w in self.models.values()):
            raise ValueError("model weights must be > 0")
        return self


def load_scenario(path: Path | str, **overrides: Any) -> LoadConfig:
    data = yaml.safe_load(Path(path).read_text()) or {}
    data.update({k: v for k, v in overrides.items() if v is not None})
    return LoadConfig(**data)
