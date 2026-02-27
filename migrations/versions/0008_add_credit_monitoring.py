"""add credit monitoring tables

Revision ID: 0008_add_credit_monitoring
Revises: 0007_add_status_to_clients
Create Date: 2026-02-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_add_credit_monitoring"
down_revision = "0007_add_status_to_clients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credit_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "api_key_id",
            sa.Integer(),
            sa.ForeignKey("api_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("remaining_credits", sa.Integer(), nullable=False),
        sa.Column("plan_credits", sa.Integer(), nullable=False),
        sa.Column("billing_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("billing_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "snapshot_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("fetch_success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("idx_credit_snapshots_api_key_id", "credit_snapshots", ["api_key_id"])
    op.create_index("idx_credit_snapshots_snapshot_at", "credit_snapshots", ["snapshot_at"])

    op.add_column("api_keys", sa.Column("last_credit_snapshot_id", sa.Integer(), nullable=True))
    op.add_column("api_keys", sa.Column("last_credit_check_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("cached_remaining_credits", sa.Integer(), nullable=True))
    op.add_column("api_keys", sa.Column("cached_plan_credits", sa.Integer(), nullable=True))
    op.add_column("api_keys", sa.Column("next_refresh_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "next_refresh_at")
    op.drop_column("api_keys", "cached_plan_credits")
    op.drop_column("api_keys", "cached_remaining_credits")
    op.drop_column("api_keys", "last_credit_check_at")
    op.drop_column("api_keys", "last_credit_snapshot_id")

    op.drop_index("idx_credit_snapshots_snapshot_at", table_name="credit_snapshots")
    op.drop_index("idx_credit_snapshots_api_key_id", table_name="credit_snapshots")
    op.drop_table("credit_snapshots")

