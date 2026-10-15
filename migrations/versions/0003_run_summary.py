"""add runs.summary and runs.report_dir

Revision ID: 0003_run_summary
Revises: 0002_client_request_id
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_run_summary"
down_revision = "0002_client_request_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("summary", postgresql.JSONB(), nullable=True))
    op.add_column("runs", sa.Column("report_dir", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "report_dir")
    op.drop_column("runs", "summary")
