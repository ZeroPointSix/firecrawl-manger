"""init

Revision ID: 0001_init
Revises:
Create Date: 2026-02-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("api_key_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("api_key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("api_key_last4", sa.String(length=4), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("plan_type", sa.String(length=32), nullable=False, server_default="free"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("daily_quota", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("daily_usage", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("quota_reset_at", sa.Date(), nullable=True),
        sa.Column("max_concurrent", sa.Integer(), nullable=False, server_default=sa.text("2")),
        sa.Column("current_concurrent", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rate_limit_per_min", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("rate_limit_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_requests", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )
    op.create_index("idx_api_keys_active", "api_keys", ["is_active", "status"])
    op.create_index("idx_api_keys_cooldown", "api_keys", ["cooldown_until"])
    op.create_index("idx_api_keys_quota_reset", "api_keys", ["quota_reset_at"])

    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("daily_quota", sa.Integer(), nullable=True),
        sa.Column("daily_usage", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("quota_reset_at", sa.Date(), nullable=True),
        sa.Column("rate_limit_per_min", sa.Integer(), nullable=False, server_default=sa.text("60")),
        sa.Column("max_concurrent", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_clients_active", "clients", ["is_active"])

    op.create_table(
        "request_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=True),
        sa.Column("api_key_id", sa.Integer(), sa.ForeignKey("api_keys.id"), nullable=True),
        sa.Column("endpoint", sa.String(length=32), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("idx_request_logs_request_id", "request_logs", ["request_id"])
    op.create_index("idx_request_logs_created_at", "request_logs", ["created_at"])

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("client_id", "idempotency_key", name="uq_idempotency_client_key"),
    )
    op.create_index(
        "idx_idempotency_expires_at", "idempotency_records", ["expires_at"], unique=False
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("idx_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("idx_idempotency_expires_at", table_name="idempotency_records")
    op.drop_table("idempotency_records")

    op.drop_index("idx_request_logs_created_at", table_name="request_logs")
    op.drop_index("idx_request_logs_request_id", table_name="request_logs")
    op.drop_table("request_logs")

    op.drop_index("idx_clients_active", table_name="clients")
    op.drop_table("clients")

    op.drop_index("idx_api_keys_quota_reset", table_name="api_keys")
    op.drop_index("idx_api_keys_cooldown", table_name="api_keys")
    op.drop_index("idx_api_keys_active", table_name="api_keys")
    op.drop_table("api_keys")

