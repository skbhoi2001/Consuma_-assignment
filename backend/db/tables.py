"""PostgreSQL tables.

Hot-path request state lives in Redis (commit 6+). Postgres holds durable records:
model config + limit history, batches and their final results, callback attempts,
and load-test runs/metrics for reports. Status columns are plain strings holding
values from backend.common.states (no PG enums -> no enum migrations later).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base

TZ = DateTime(timezone=True)


class ModelRow(Base):
    __tablename__ = "models"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    rpm: Mapped[int] = mapped_column(BigInteger)
    tpm: Mapped[int] = mapped_column(BigInteger)
    max_queue: Mapped[int] = mapped_column(Integer)
    max_wait_seconds: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class ModelLimitChange(Base):
    __tablename__ = "model_limit_changes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model: Mapped[str] = mapped_column(ForeignKey("models.name", ondelete="CASCADE"), index=True)
    old_rpm: Mapped[int | None] = mapped_column(BigInteger)
    new_rpm: Mapped[int] = mapped_column(BigInteger)
    old_tpm: Mapped[int | None] = mapped_column(BigInteger)
    new_tpm: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(16))  # "config" | "api"
    changed_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), index=True)


class BatchRow(Base):
    __tablename__ = "batches"

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    callback_url: Mapped[str | None] = mapped_column(Text)
    callback_status: Mapped[str] = mapped_column(String(32))
    total: Mapped[int] = mapped_column(Integer)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    expired: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZ)
    callback_delivered_at: Mapped[datetime | None] = mapped_column(TZ)


class RequestRow(Base):
    """Final result of a request. Primary key on request_id => each id stored exactly once."""

    __tablename__ = "requests"

    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    client_request_id: Mapped[str | None] = mapped_column(String(128))
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("batches.batch_id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    estimated_tokens: Mapped[int] = mapped_column(Integer)
    actual_tokens: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(TZ)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    latency_ms: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        Index("ix_requests_batch_id_request_id", "batch_id", "request_id"),  # paged results
        Index("ix_requests_model_finished_at", "model", "finished_at"),
    )


class CallbackAttempt(Base):
    __tablename__ = "callback_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.batch_id", ondelete="CASCADE"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    status_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    sent_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    next_retry_at: Mapped[datetime | None] = mapped_column(TZ)

    __table_args__ = (UniqueConstraint("batch_id", "attempt_no"),)


class Run(Base):
    """One load-test / scenario run."""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # running | finished | aborted
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    machine_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(TZ)


class RunMetric(Base):
    """Per-second, per-model time series for a run (drives charts + 60s-window checks)."""

    __tablename__ = "run_metrics"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    ts_second: Mapped[datetime] = mapped_column(TZ, primary_key=True)
    model: Mapped[str] = mapped_column(String(64), primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    configured_rpm: Mapped[int] = mapped_column(BigInteger)
    configured_tpm: Mapped[int] = mapped_column(BigInteger)
    latency_p50_ms: Mapped[float | None] = mapped_column(Float)
    latency_p95_ms: Mapped[float | None] = mapped_column(Float)
    latency_p99_ms: Mapped[float | None] = mapped_column(Float)


class RunResult(Base):
    """PASS/FAIL checks for a run."""

    __tablename__ = "run_results"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    check_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    measured: Mapped[str | None] = mapped_column(Text)
    expected: Mapped[str | None] = mapped_column(Text)
