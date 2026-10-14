"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "models",
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("rpm", sa.BigInteger(), nullable=False),
        sa.Column("tpm", sa.BigInteger(), nullable=False),
        sa.Column("max_queue", sa.Integer(), nullable=False),
        sa.Column("max_wait_seconds", sa.Float(), nullable=False),
        sa.Column("updated_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("name", name="pk_models"),
    )

    op.create_table(
        "model_limit_changes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("old_rpm", sa.BigInteger(), nullable=True),
        sa.Column("new_rpm", sa.BigInteger(), nullable=False),
        sa.Column("old_tpm", sa.BigInteger(), nullable=True),
        sa.Column("new_tpm", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("changed_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["model"],
            ["models.name"],
            name="fk_model_limit_changes_model_models",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_limit_changes"),
    )
    op.create_index("ix_model_limit_changes_model", "model_limit_changes", ["model"])
    op.create_index("ix_model_limit_changes_changed_at", "model_limit_changes", ["changed_at"])

    op.create_table(
        "batches",
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("callback_url", sa.Text(), nullable=True),
        sa.Column("callback_status", sa.String(32), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("expired", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", TZ, nullable=True),
        sa.Column("callback_delivered_at", TZ, nullable=True),
        sa.PrimaryKeyConstraint("batch_id", name="pk_batches"),
    )
    op.create_index("ix_batches_status", "batches", ["status"])
    op.create_index("ix_batches_created_at", "batches", ["created_at"])

    op.create_table(
        "requests",
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("actual_tokens", sa.Integer(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("submitted_at", TZ, nullable=False),
        sa.Column("finished_at", TZ, nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batches.batch_id"],
            name="fk_requests_batch_id_batches",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("request_id", name="pk_requests"),
    )
    op.create_index("ix_requests_batch_id_request_id", "requests", ["batch_id", "request_id"])
    op.create_index("ix_requests_model_finished_at", "requests", ["model", "finished_at"])

    op.create_table(
        "callback_attempts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("sent_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("next_retry_at", TZ, nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batches.batch_id"],
            name="fk_callback_attempts_batch_id_batches",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_callback_attempts"),
        sa.UniqueConstraint("batch_id", "attempt_no", name="uq_callback_attempts_batch_id"),
    )

    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("scenario", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("machine_info", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", TZ, nullable=True),
        sa.PrimaryKeyConstraint("run_id", name="pk_runs"),
    )

    op.create_table(
        "run_metrics",
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("ts_second", TZ, nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("tokens", sa.BigInteger(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("configured_rpm", sa.BigInteger(), nullable=False),
        sa.Column("configured_tpm", sa.BigInteger(), nullable=False),
        sa.Column("latency_p50_ms", sa.Float(), nullable=True),
        sa.Column("latency_p95_ms", sa.Float(), nullable=True),
        sa.Column("latency_p99_ms", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            name="fk_run_metrics_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "ts_second", "model", name="pk_run_metrics"),
    )

    op.create_table(
        "run_results",
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("check_name", sa.String(64), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("measured", sa.Text(), nullable=True),
        sa.Column("expected", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            name="fk_run_results_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "check_name", name="pk_run_results"),
    )


def downgrade() -> None:
    op.drop_table("run_results")
    op.drop_table("run_metrics")
    op.drop_table("runs")
    op.drop_table("callback_attempts")
    op.drop_index("ix_requests_model_finished_at", table_name="requests")
    op.drop_index("ix_requests_batch_id_request_id", table_name="requests")
    op.drop_table("requests")
    op.drop_index("ix_batches_created_at", table_name="batches")
    op.drop_index("ix_batches_status", table_name="batches")
    op.drop_table("batches")
    op.drop_index("ix_model_limit_changes_changed_at", table_name="model_limit_changes")
    op.drop_index("ix_model_limit_changes_model", table_name="model_limit_changes")
    op.drop_table("model_limit_changes")
    op.drop_table("models")
