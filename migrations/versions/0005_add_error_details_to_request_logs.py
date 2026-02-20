"""add error_details to request_logs

Revision ID: 0005_add_error_details_to_request_logs
Revises: 0004_add_client_id_to_api_keys
Create Date: 2026-02-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_add_error_details_to_request_logs"
down_revision = "0004_add_client_id_to_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("error_details", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("request_logs", "error_details")

