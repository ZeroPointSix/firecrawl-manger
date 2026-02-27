"""add status to clients

Revision ID: 0007_add_status_to_clients
Revises: 0006_add_upstream_resource_bindings
Create Date: 2026-02-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_add_status_to_clients"
down_revision = "0006_add_upstream_resource_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 添加 status 列，默认值为 "active"
    op.add_column(
        "clients",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )

    # 更新现有数据：根据 is_active 设置 status
    # is_active=False 的记录设置为 "disabled"（因为之前没有区分 disabled 和 deleted）
    op.execute(
        """
        UPDATE clients
        SET status = CASE
            WHEN is_active THEN 'active'
            ELSE 'disabled'
        END
        """
    )


def downgrade() -> None:
    op.drop_column("clients", "status")
