"""Test callback receiver - stands in for a client's callback endpoint.

Rejects the first `fail_first_n` delivery attempts *per batch* with HTTP 500, then accepts.
Run:  uvicorn backend.receiver.app:create_app --factory --port 9100
"""

import time
from collections import defaultdict
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.common.settings import get_settings


class ReceiverConfig(BaseModel):
    fail_first_n: int = Field(ge=0)


def create_app(fail_first_n: int | None = None) -> FastAPI:
    app = FastAPI(title="Callback Receiver (test)", version="0.1.0")
    state: dict[str, Any] = {
        "fail_first_n": (
            get_settings().receiver_fail_first_n if fail_first_n is None else fail_first_n
        ),
        "attempts": defaultdict(int),
        "received": [],
    }
    app.state.receiver = state

    @app.post("/callbacks/receive")
    async def receive(request: Request) -> JSONResponse:
        body = await request.json()
        batch_id = str(body.get("batch_id"))
        state["attempts"][batch_id] += 1
        seen = state["attempts"][batch_id]
        if seen <= state["fail_first_n"]:
            return JSONResponse({"accepted": False, "attempt_seen": seen}, status_code=500)
        state["received"].append(
            {
                "received_at": time.time(),
                "attempt_seen": seen,
                "idempotency_key": request.headers.get("idempotency-key"),
                "payload": body,
            }
        )
        return JSONResponse({"accepted": True, "attempt_seen": seen})

    @app.put("/callbacks/config")
    async def configure(cfg: ReceiverConfig) -> dict[str, int]:
        state["fail_first_n"] = cfg.fail_first_n
        return {"fail_first_n": cfg.fail_first_n}

    @app.get("/callbacks/received")
    async def received() -> dict[str, Any]:
        return {"attempts": dict(state["attempts"]), "received": state["received"]}

    @app.post("/callbacks/reset")
    async def reset() -> dict[str, str]:
        state["attempts"].clear()
        state["received"].clear()
        return {"status": "reset"}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
