"""add account fields to api_keys

Revision ID: 0003_add_account_fields_to_api_keys
Revises: 0002_add_retry_count_to_request_logs
Create Date: 2026-02-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_add_account_fields_to_api_keys"
down_revision = "0002_add_retry_count_to_request_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("account_username", sa.String(length=255), nullable=True))
    op.add_column("api_keys", sa.Column("account_password_ciphertext", sa.LargeBinary(), nullable=True))
    op.add_column("api_keys", sa.Column("account_verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "account_verified_at")
    op.drop_column("api_keys", "account_password_ciphertext")
    op.drop_column("api_keys", "account_username")

