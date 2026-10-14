import pytest
from sqlalchemy import func, select

import backend.db.session as db_session
from backend.common.settings import get_settings
from backend.db.seed import seed_models
from backend.db.tables import ModelLimitChange, ModelRow
from tests.conftest import TEST_DATABASE_URL, ensure_database, requires_postgres
from tests.test_db import alembic

pytestmark = requires_postgres


@pytest.fixture
async def seeded_db(monkeypatch):
    await ensure_database(TEST_DATABASE_URL)
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    await db_session.dispose_engine()
    yield
    await db_session.dispose_engine()
    get_settings.cache_clear()
    alembic("downgrade", "base")


async def test_seed_is_idempotent(seeded_db):
    first = await seed_models()
    assert set(first.values()) == {"inserted"}

    second = await seed_models()
    assert set(second.values()) == {"unchanged"}

    async with db_session.get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(ModelRow)) == len(first)
        # one history row per model (the initial insert), none for the no-op second run
        assert await s.scalar(select(func.count()).select_from(ModelLimitChange)) == len(first)


async def test_seed_records_limit_change(seeded_db, tmp_path, monkeypatch):
    await seed_models()

    changed = tmp_path / "models.yaml"
    changed.write_text(
        "models:\n"
        "  - {name: model-a, rpm: 5000, tpm: 60000000, max_queue: 200000, max_wait_seconds: 120}\n"
    )
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(changed))
    get_settings.cache_clear()

    assert (await seed_models()) == {"model-a": "updated"}

    async with db_session.get_sessionmaker()() as s:
        row = await s.get(ModelRow, "model-a")
        assert row.rpm == 5000
        last = await s.scalar(
            select(ModelLimitChange)
            .where(ModelLimitChange.model == "model-a")
            .order_by(ModelLimitChange.id.desc())
        )
        assert (last.old_rpm, last.new_rpm) == (30000, 5000)
