"""add provider to api_keys

Revision ID: 0010_add_provider_to_api_keys
Revises: 0009_add_credit_monitoring_indexes
Create Date: 2026-03-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_add_provider_to_api_keys"
down_revision = "0009_add_credit_monitoring_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("provider", sa.String(32), nullable=False, server_default="firecrawl"),
    )
    op.create_index("idx_api_keys_provider", "api_keys", ["provider"])


def downgrade() -> None:
    op.drop_index("idx_api_keys_provider", table_name="api_keys")
    op.drop_column("api_keys", "provider")
