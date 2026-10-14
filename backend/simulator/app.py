"""Provider simulator HTTP API.

Run:  uvicorn backend.simulator.app:create_app --factory --port 9000
"""

import asyncio
import math
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.common.schemas import MAX_ESTIMATED_TOKENS, ModelName, RequestId
from backend.common.settings import get_settings
from backend.simulator.config import SimModelConfig, SimModelUpdate, load_sim_config
from backend.simulator.provider import ProviderSimulator


class InferenceCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: RequestId
    model: ModelName
    estimated_tokens: int = Field(gt=0, le=MAX_ESTIMATED_TOKENS)
    payload: dict[str, Any] = Field(default_factory=dict)


def create_app(models: list[SimModelConfig] | None = None, *, seed: int | None = None) -> FastAPI:
    settings = get_settings()
    if models is None:
        models = load_sim_config(settings.simulator_config_path)
    sim = ProviderSimulator(models, seed=seed if seed is not None else settings.simulator_seed)

    app = FastAPI(title="Provider Simulator", version="0.1.0")
    app.state.sim = sim

    @app.post("/sim/inference")
    async def inference(call: InferenceCall) -> JSONResponse:
        outcome = sim.handle(call.model, call.estimated_tokens)
        if outcome.latency_ms > 0:
            await asyncio.sleep(outcome.latency_ms / 1000)

        body: dict[str, Any] = {
            "request_id": call.request_id,
            "model": call.model,
            "latency_ms": round(outcome.latency_ms, 3),
        }
        if outcome.status_code == 200:
            body["actual_tokens"] = outcome.actual_tokens
            body["output"] = {"text": f"simulated completion for {call.request_id}"}
            return JSONResponse(body)

        body["error"] = outcome.kind
        headers = None
        if outcome.retry_after is not None:
            body["retry_after"] = round(outcome.retry_after, 3)
            headers = {"Retry-After": str(max(1, math.ceil(outcome.retry_after)))}
        return JSONResponse(body, status_code=outcome.status_code, headers=headers)

    @app.get("/sim/models")
    async def list_models() -> list[SimModelConfig]:
        return list(sim.models.values())

    @app.get("/sim/models/{name}")
    async def get_model(name: str) -> SimModelConfig:
        if name not in sim.models:
            raise HTTPException(404, f"unknown model {name}")
        return sim.models[name]

    @app.put("/sim/models/{name}")
    async def update_model(name: str, update: SimModelUpdate) -> SimModelConfig:
        if name not in sim.models:
            raise HTTPException(404, f"unknown model {name}")
        try:
            return sim.update_model(name, update)
        except ValueError as exc:  # e.g. error rates now sum to > 1
            raise HTTPException(422, str(exc)) from exc

    @app.get("/sim/stats")
    async def stats() -> dict[str, dict]:
        return sim.stats()

    @app.post("/sim/reset")
    async def reset() -> dict[str, str]:
        sim.reset()
        return {"status": "reset"}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
