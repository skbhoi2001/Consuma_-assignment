"""Integration tests against a real Postgres (database: inference_test)."""

import os
import subprocess
import sys

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.common.ids import new_batch_id, utc_now
from backend.db.base import Base
from backend.db.tables import BatchRow, RequestRow
from tests.conftest import TEST_DATABASE_URL, ensure_database, requires_postgres

pytestmark = requires_postgres

EXPECTED_TABLES = {
    "models",
    "model_limit_changes",
    "batches",
    "requests",
    "callback_attempts",
    "runs",
    "run_metrics",
    "run_results",
}


def alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    subprocess.run([sys.executable, "-m", "alembic", *args], check=True, env=env)


@pytest.fixture
async def migrated_engine():
    await ensure_database(TEST_DATABASE_URL)
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    engine = create_async_engine(TEST_DATABASE_URL)
    yield engine
    await engine.dispose()
    alembic("downgrade", "base")


async def test_migration_creates_all_tables(migrated_engine):
    async with migrated_engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert EXPECTED_TABLES <= tables


async def test_migration_matches_models(migrated_engine):
    """The hand-written migration and the SQLAlchemy models must not drift apart."""
    async with migrated_engine.connect() as conn:
        diff = await conn.run_sync(
            lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata)
        )
    assert diff == []


async def test_request_id_stored_exactly_once(migrated_engine):
    sm = async_sessionmaker(migrated_engine, expire_on_commit=False)
    bid = new_batch_id()
    now = utc_now()

    async with sm() as s, s.begin():
        s.add(BatchRow(batch_id=bid, status="processing", callback_status="pending", total=1))
    async with sm() as s, s.begin():
        s.add(
            RequestRow(
                request_id="req_dup",
                batch_id=bid,
                model="model-a",
                state="succeeded",
                attempts=1,
                estimated_tokens=1000,
                submitted_at=now,
            )
        )

    with pytest.raises(IntegrityError):
        async with sm() as s, s.begin():
            s.add(
                RequestRow(
                    request_id="req_dup",
                    batch_id=bid,
                    model="model-a",
                    state="failed",
                    attempts=1,
                    estimated_tokens=1000,
                    submitted_at=now,
                )
            )

    async with sm() as s:
        rows = (await s.scalars(select(RequestRow).where(RequestRow.batch_id == bid))).all()
    assert [r.request_id for r in rows] == ["req_dup"]
