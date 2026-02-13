"""add client_id to api_keys

Revision ID: 0004_add_client_id_to_api_keys
Revises: 0003_add_account_fields_to_api_keys
Create Date: 2026-02-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_add_client_id_to_api_keys"
down_revision = "0003_add_account_fields_to_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("client_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_api_keys_client_id", "api_keys", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_client_id", table_name="api_keys")
    op.drop_column("api_keys", "client_id")
