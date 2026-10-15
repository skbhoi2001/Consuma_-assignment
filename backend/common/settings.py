"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastructure
    database_url: str = "postgresql+asyncpg://postgres@localhost:5432/inference"
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = Field(100, gt=0)
    gateway_port: int = 8000
    simulator_url: str = "http://localhost:9000"
    simulator_port: int = 9000
    receiver_port: int = 9100
    log_level: str = "INFO"

    # Models
    models_config_path: Path = Path("config/models.yaml")

    # Rate limiting: aim slightly below the limit for safety headroom
    target_utilization: float = Field(0.98, gt=0, le=1)
    # Max share of the per-minute limit sent in any one second, as a multiple of limit/60.
    # 1.0 = perfectly smooth pacing; None (unset) = no per-second cap.
    limiter_burst_factor: float | None = Field(1.0, gt=0)

    # Provider simulator
    simulator_config_path: Path = Path("config/simulator.yaml")
    simulator_seed: int | None = None

    # Input limits
    max_batch_size: int = Field(100_000, gt=0)
    max_estimated_tokens: int = Field(1_000_000, gt=0)

    # Provider-call retries (transient failures)
    max_attempts: int = Field(4, ge=1)
    retry_base_delay_ms: int = Field(200, gt=0)
    retry_max_delay_ms: int = Field(10_000, gt=0)

    # Callback delivery retries
    callback_max_attempts: int = Field(10, ge=1)
    callback_base_delay_ms: int = Field(1_000, gt=0)
    callback_max_delay_ms: int = Field(60_000, gt=0)
    callback_timeout_seconds: float = Field(5.0, gt=0)

    # Dispatcher
    max_inflight_per_model: int = Field(1_000, gt=0)
    provider_timeout_seconds: float = Field(30.0, gt=0)
    provider_max_connections: int = Field(512, gt=0)
    request_ttl_seconds: int = Field(86_400, gt=0)  # live state kept in Redis this long
    results_flush_interval_ms: int = Field(250, gt=0)
    results_flush_max_rows: int = Field(2_000, gt=0)

    # Push limit changes made through the gateway API to the simulator too
    sync_simulator_limits: bool = True
    metrics_retention_seconds: int = Field(3_600, gt=0)

    # Public URL of the gateway, used to build results_url in batch status and callbacks
    public_base_url: str = "http://localhost:8000"

    # Test callback receiver: reject this many delivery attempts per batch before accepting
    receiver_fail_first_n: int = Field(0, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
