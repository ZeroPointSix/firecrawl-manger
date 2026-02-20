"""add retry_count to request_logs

Revision ID: 0002_add_retry_count_to_request_logs
Revises: 0001_init
Create Date: 2026-02-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_add_retry_count_to_request_logs"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_logs",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("request_logs", "retry_count")

