"""Model + limit routes: list models, change limits at runtime, limit history."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from backend.common.schemas import LimitUpdate
from backend.db.tables import ModelLimitChange, ModelRow
from backend.gateway.runtime import Runtime

router = APIRouter(prefix="/v1/models", tags=["models"])


def _rt(request: Request) -> Runtime:
    return request.app.state.rt


def model_info(rt: Runtime, name: str) -> dict:
    cfg = rt.dispatcher.models[name]
    lane = rt.dispatcher.lane_stats()[name]
    live = rt.metrics.live().get(name, {})
    return {
        "name": name,
        "rpm": cfg.rpm,
        "tpm": cfg.tpm,
        "effective_rpm": lane["effective_rpm"],
        "effective_tpm": lane["effective_tpm"],
        "max_queue": cfg.max_queue,
        "max_wait_seconds": cfg.max_wait_seconds,
        "queued": lane["queued"],
        "inflight": lane["inflight"],
        "retry_wait": lane["retry_wait"],
        "window_requests": lane["window_requests"],
        "window_tokens": lane["window_tokens"],
        "rolling_60s_requests": live.get("rolling_60s_requests", 0),
        "rolling_60s_tokens": live.get("rolling_60s_tokens", 0),
    }


@router.get("")
async def list_models(request: Request) -> list[dict]:
    rt = _rt(request)
    return [model_info(rt, name) for name in rt.dispatcher.models]


@router.get("/{model}")
async def get_model(model: str, request: Request) -> dict:
    rt = _rt(request)
    if model not in rt.dispatcher.models:
        raise HTTPException(404, f"unknown model {model}")
    return model_info(rt, model)


@router.patch("/{model}/limits")
async def update_limits(model: str, body: LimitUpdate, request: Request) -> dict:
    """Change RPM and/or TPM while the system runs. Applies on the scheduler's next check,
    is recorded in the limit history, and is pushed to the simulator."""
    rt = _rt(request)
    if model not in rt.dispatcher.models:
        raise HTTPException(404, f"unknown model {model}")
    old = rt.dispatcher.models[model]
    wanted = {"rpm": body.rpm, "tpm": body.tpm}
    lower = {k: v for k, v in wanted.items() if v is not None and v < getattr(old, k)}
    higher = {k: v for k, v in wanted.items() if v is not None and v > getattr(old, k)}

    # Never let our limit sit above the provider's, even for a moment:
    # decreases apply here first (stop sending), then at the provider;
    # increases apply at the provider first, then here.
    if lower:
        rt.dispatcher.update_limits(model, **lower)
    synced = await rt.sync_simulator_limits(model, body.rpm, body.tpm)
    if higher:
        rt.dispatcher.update_limits(model, **higher)
    new = rt.dispatcher.models[model]

    async with rt.sessionmaker() as session, session.begin():
        row = await session.get(ModelRow, model)
        if row is None:
            session.add(
                ModelRow(
                    name=model,
                    rpm=new.rpm,
                    tpm=new.tpm,
                    max_queue=new.max_queue,
                    max_wait_seconds=new.max_wait_seconds,
                )
            )
        else:
            row.rpm, row.tpm, row.updated_at = new.rpm, new.tpm, datetime.now(UTC)
        await session.flush()
        session.add(
            ModelLimitChange(
                model=model,
                old_rpm=old.rpm,
                new_rpm=new.rpm,
                old_tpm=old.tpm,
                new_tpm=new.tpm,
                source="api",
            )
        )

    return {**model_info(rt, model), "simulator_synced": synced}


@router.get("/{model}/limits/history")
async def limit_history(model: str, request: Request, limit: int = 100) -> list[dict]:
    rt = _rt(request)
    async with rt.sessionmaker() as session:
        rows = (
            await session.scalars(
                select(ModelLimitChange)
                .where(ModelLimitChange.model == model)
                .order_by(ModelLimitChange.id.desc())
                .limit(limit)
            )
        ).all()
    return [
        {
            "changed_at": r.changed_at,
            "source": r.source,
            "old_rpm": r.old_rpm,
            "new_rpm": r.new_rpm,
            "old_tpm": r.old_tpm,
            "new_tpm": r.new_tpm,
        }
        for r in rows
    ]
