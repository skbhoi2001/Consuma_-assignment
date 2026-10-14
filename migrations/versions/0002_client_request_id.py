"""add requests.client_request_id

Revision ID: 0002_client_request_id
Revises: 0001_initial
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_client_request_id"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("requests", sa.Column("client_request_id", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("requests", "client_request_id")
