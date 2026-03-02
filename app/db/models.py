from __future__ import annotations

from datetime import date, datetime, timezone


def _utc_now() -> datetime:
    """返回当前 UTC 时间（timezone-aware）"""
    return datetime.now(timezone.utc)

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)

    api_key_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    api_key_last4: Mapped[str] = mapped_column(String(4), nullable=False)

    account_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_password_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    account_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan_type: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    daily_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    daily_usage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_reset_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    current_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    rate_limit_per_min: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    rate_limit_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    total_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    last_credit_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_credit_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cached_remaining_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_plan_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    client: Mapped["Client | None"] = relationship(back_populates="api_keys")
    request_logs: Mapped[list["RequestLog"]] = relationship(back_populates="api_key")
    credit_snapshots: Mapped[list["CreditSnapshot"]] = relationship(
        back_populates="api_key",
        cascade="all, delete-orphan",
    )


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    daily_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_usage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_reset_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="client")
    request_logs: Mapped[list["RequestLog"]] = relationship(back_populates="client")
    idempotency_records: Mapped[list["IdempotencyRecord"]] = relationship(back_populates="client")


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)

    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id"), nullable=True)

    endpoint: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)

    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    client: Mapped[Client | None] = relationship(back_populates="request_logs")
    api_key: Mapped[ApiKey | None] = relationship(back_populates="request_logs")


class CreditSnapshot(Base):
    __tablename__ = "credit_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_id: Mapped[int] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )

    remaining_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    billing_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    fetch_success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    api_key: Mapped["ApiKey"] = relationship(back_populates="credit_snapshots")


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("client_id", "idempotency_key", name="uq_idempotency_client_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped[Client] = relationship(back_populates="idempotency_records")


class UpstreamResourceBinding(Base):
    __tablename__ = "upstream_resource_bindings"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "resource_type",
            "resource_id",
            name="uq_upstream_resource_binding",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"), nullable=False)

    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped[Client] = relationship()
    api_key: Mapped[ApiKey] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
