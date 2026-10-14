"""Sync models from config/models.yaml into the `models` table.

Run:  python -m backend.db.seed
New models are inserted; changed limits are updated and logged in model_limit_changes.
Running it twice with the same config changes nothing (idempotent).
"""

import asyncio

from sqlalchemy import select

from backend.common.ids import utc_now
from backend.common.model_registry import load_models
from backend.common.settings import get_settings
from backend.db.session import dispose_engine, get_sessionmaker
from backend.db.tables import ModelLimitChange, ModelRow


async def seed_models() -> dict[str, str]:
    """Returns {model_name: "inserted" | "updated" | "unchanged"}."""
    configs = load_models(get_settings().models_config_path)
    outcome: dict[str, str] = {}
    history: list[ModelLimitChange] = []

    async with get_sessionmaker()() as session, session.begin():
        existing = {row.name: row for row in (await session.scalars(select(ModelRow))).all()}

        for name, cfg in configs.items():
            row = existing.get(name)
            if row is None:
                session.add(
                    ModelRow(
                        name=name,
                        rpm=cfg.rpm,
                        tpm=cfg.tpm,
                        max_queue=cfg.max_queue,
                        max_wait_seconds=cfg.max_wait_seconds,
                    )
                )
                history.append(
                    ModelLimitChange(model=name, new_rpm=cfg.rpm, new_tpm=cfg.tpm, source="config")
                )
                outcome[name] = "inserted"
                continue

            current = (row.rpm, row.tpm, row.max_queue, row.max_wait_seconds)
            wanted = (cfg.rpm, cfg.tpm, cfg.max_queue, cfg.max_wait_seconds)
            if current == wanted:
                outcome[name] = "unchanged"
                continue

            if (row.rpm, row.tpm) != (cfg.rpm, cfg.tpm):
                history.append(
                    ModelLimitChange(
                        model=name,
                        old_rpm=row.rpm,
                        new_rpm=cfg.rpm,
                        old_tpm=row.tpm,
                        new_tpm=cfg.tpm,
                        source="config",
                    )
                )
            row.rpm, row.tpm = cfg.rpm, cfg.tpm
            row.max_queue, row.max_wait_seconds = cfg.max_queue, cfg.max_wait_seconds
            row.updated_at = utc_now()
            outcome[name] = "updated"

        # models must exist before history rows that reference them (FK)
        await session.flush()
        session.add_all(history)

    return outcome


async def _main() -> None:
    try:
        for name, result in (await seed_models()).items():
            print(f"{name:20s} {result}")
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
