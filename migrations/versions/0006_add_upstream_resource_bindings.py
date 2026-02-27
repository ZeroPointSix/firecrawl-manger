"""add upstream resource bindings

Revision ID: 0006_add_upstream_resource_bindings
Revises: 0005_add_error_details_to_request_logs
Create Date: 2026-02-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_add_upstream_resource_bindings"
down_revision = "0005_add_error_details_to_request_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upstream_resource_bindings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("api_key_id", sa.Integer(), sa.ForeignKey("api_keys.id"), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "client_id",
            "resource_type",
            "resource_id",
            name="uq_upstream_resource_binding",
        ),
    )
    op.create_index(
        "idx_upstream_resource_binding_lookup",
        "upstream_resource_bindings",
        ["client_id", "resource_type", "resource_id"],
        unique=False,
    )
    op.create_index(
        "idx_upstream_resource_binding_expires_at",
        "upstream_resource_bindings",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_upstream_resource_binding_expires_at", table_name="upstream_resource_bindings")
    op.drop_index("idx_upstream_resource_binding_lookup", table_name="upstream_resource_bindings")
    op.drop_table("upstream_resource_bindings")

