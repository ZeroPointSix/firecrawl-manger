"""add credit monitoring indexes

Revision ID: 0009_add_credit_monitoring_indexes
Revises: 0008_add_credit_monitoring
Create Date: 2026-02-26
"""

from __future__ import annotations

from alembic import op

revision = "0009_add_credit_monitoring_indexes"
down_revision = "0008_add_credit_monitoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_credit_snapshots_api_key_id_snapshot_at",
        "credit_snapshots",
        ["api_key_id", "snapshot_at"],
    )
    op.create_index("idx_api_keys_next_refresh_at", "api_keys", ["next_refresh_at"])


def downgrade() -> None:
    op.drop_index("idx_api_keys_next_refresh_at", table_name="api_keys")
    op.drop_index("idx_credit_snapshots_api_key_id_snapshot_at", table_name="credit_snapshots")
