"""Builds and owns every long-running component of the gateway."""

import os
import time

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.callbacks.service import CallbackService
from backend.common.model_registry import load_models
from backend.common.schemas import ModelConfig
from backend.common.settings import Settings, get_settings
from backend.db.redis_client import get_redis
from backend.db.session import get_sessionmaker
from backend.dispatcher.dispatcher import Dispatcher
from backend.dispatcher.limiter import LimiterRegistry
from backend.dispatcher.provider_client import (
    HttpProviderClient,
    InProcessProviderClient,
    ProviderClient,
)
from backend.dispatcher.results_writer import ResultsWriter
from backend.dispatcher.store import RedisStateStore
from backend.metrics.collector import MetricsCollector
from backend.simulator.config import SimModelUpdate


class Runtime:
    """Everything is injectable so tests can swap the provider, Redis, DB and HTTP client."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        models: dict[str, ModelConfig] | None = None,
        provider: ProviderClient | None = None,
        redis: Redis | None = None,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        callback_http: httpx.AsyncClient | None = None,
    ) -> None:
        s = self.settings = settings or get_settings()
        self.models = models or load_models(s.models_config_path)
        self.redis = redis or get_redis()
        self.sessionmaker = sessionmaker or get_sessionmaker()
        self.provider = provider or HttpProviderClient(
            s.simulator_url,
            timeout=s.provider_timeout_seconds,
            max_connections=s.provider_max_connections,
        )
        self.callback_http = callback_http or httpx.AsyncClient()
        self.admin_http = httpx.AsyncClient(timeout=5.0)
        self.pid = os.getpid()
        self.started_at = time.time()

        self.registry = LimiterRegistry(
            self.models, utilization=s.target_utilization, burst_factor=s.limiter_burst_factor
        )
        self.store = RedisStateStore(self.redis, ttl_seconds=s.request_ttl_seconds)
        self.writer = ResultsWriter(
            self.sessionmaker,
            flush_interval=s.results_flush_interval_ms / 1000,
            max_rows=s.results_flush_max_rows,
        )
        self.metrics = MetricsCollector(self.models, retention_seconds=s.metrics_retention_seconds)
        self.callbacks = CallbackService(
            self.store, self.sessionmaker, self.writer, s, self.callback_http
        )
        self.dispatcher = Dispatcher(
            self.models,
            self.registry,
            self.provider,
            self.store,
            s,
            writer=self.writer,
            metrics=self.metrics,
            on_batch_complete=self.callbacks.batch_completed,
        )

    async def start(self) -> None:
        self.writer.start()
        await self.dispatcher.start()
        self.callbacks.start()

    async def stop(self) -> None:
        await self.dispatcher.stop()
        await self.callbacks.stop()
        await self.writer.stop()
        await self.provider.aclose()
        await self.callback_http.aclose()
        await self.admin_http.aclose()

    async def sync_simulator_limits(
        self, model: str, rpm: int | None, tpm: int | None
    ) -> bool | None:
        """Push a limit change to the simulator so both sides agree.
        Returns True/False for success, or None if syncing is turned off."""
        update = {k: v for k, v in {"rpm": rpm, "tpm": tpm}.items() if v is not None}
        if isinstance(self.provider, InProcessProviderClient):
            sim = self.provider.simulator
            if model not in sim.models:
                return False
            sim.update_model(model, SimModelUpdate(**update))
            return True
        if not self.settings.sync_simulator_limits:
            return None
        try:
            r = await self.admin_http.put(
                f"{self.settings.simulator_url}/sim/models/{model}", json=update
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False
