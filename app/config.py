from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, Field, field_validator


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    trust_proxy_headers: bool = False
    enable_docs: bool = True
    enable_data_plane: bool = True
    enable_control_plane: bool = True


class FirecrawlConfig(BaseModel):
    base_url: str = "https://api.firecrawl.dev"
    timeout: int = 30
    max_retries: int = 3
    failure_threshold: int = 3
    failure_window_seconds: int = 60
    failed_cooldown_seconds: int = 60

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, v: str) -> str:
        normalized = v.strip()
        if not normalized:
            raise ValueError("firecrawl.base_url 不能为空")
        return normalized.rstrip("/")


class ClientAuthConfig(BaseModel):
    enabled: bool = True
    scheme: str = "bearer"


class AdminSecurityConfig(BaseModel):
    token_env: str = "FCAM_ADMIN_TOKEN"


class KeyEncryptionConfig(BaseModel):
    master_key_env: str = "FCAM_MASTER_KEY"


class RequestLimitsConfig(BaseModel):
    max_body_bytes: int = 1_048_576
    allowed_paths: list[str] = Field(
        default_factory=lambda: [
            "scrape",
            "crawl",
            "search",
            "agent",
            "map",
            "extract",
            "batch",
            "browser",
            "team",
        ]
    )


class SecurityConfig(BaseModel):
    client_auth: ClientAuthConfig = Field(default_factory=ClientAuthConfig)
    admin: AdminSecurityConfig = Field(default_factory=AdminSecurityConfig)
    key_encryption: KeyEncryptionConfig = Field(default_factory=KeyEncryptionConfig)
    request_limits: RequestLimitsConfig = Field(default_factory=RequestLimitsConfig)


class QuotaConfig(BaseModel):
    timezone: str = "UTC"
    count_mode: str = "success"  # success | attempt
    default_daily_limit: int = 5
    reset_time: str = "00:00"
    enable_quota_check: bool = True


class RateLimitConfig(BaseModel):
    cooldown_seconds: int = 60


class CreditMonitoringSmartRefreshConfig(BaseModel):
    enabled: bool = True
    high_usage_interval: int = 15
    medium_usage_interval: int = 30
    normal_usage_interval: int = 60
    low_usage_interval: int = 120


class CreditMonitoringFixedRefreshConfig(BaseModel):
    interval_minutes: int = 60


class CreditMonitoringLocalEstimationConfig(BaseModel):
    enabled: bool = True
    sync_on_request: bool = True


class CreditMonitoringConfig(BaseModel):
    enabled: bool = False
    smart_refresh: CreditMonitoringSmartRefreshConfig = Field(
        default_factory=CreditMonitoringSmartRefreshConfig
    )
    fixed_refresh: CreditMonitoringFixedRefreshConfig = Field(
        default_factory=CreditMonitoringFixedRefreshConfig
    )
    batch_size: int = 10
    batch_delay_seconds: int = 5
    local_estimation: CreditMonitoringLocalEstimationConfig = Field(
        default_factory=CreditMonitoringLocalEstimationConfig
    )
    retention_days: int = 90
    retry_delay_minutes: int = 10
    refresh_check_interval_seconds: int = 300
    min_manual_refresh_interval_seconds: int = 300
    history_max_limit: int = 500


class IdempotencyConfig(BaseModel):
    enabled: bool = True
    ttl_seconds: int = 86_400
    max_response_bytes: int = 1_048_576
    require_on: list[str] = Field(default_factory=list)  # e.g. ["crawl", "agent"]


class DatabaseConfig(BaseModel):
    path: str = "./data/api_manager.db"
    url: str | None = None


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"  # json | plain
    redact_fields: list[str] = Field(
        default_factory=lambda: ["authorization", "api_key", "x-api-key", "token", "cookie", "set-cookie"]
    )


class RetentionConfig(BaseModel):
    request_logs_days: int = 30
    audit_logs_days: int = 90


class ObservabilityConfig(BaseModel):
    metrics_enabled: bool = False
    metrics_path: str = "/metrics"
    retention: RetentionConfig = Field(default_factory=RetentionConfig)


class RedisConfig(BaseModel):
    url: str = "redis://localhost:6379/0"
    key_prefix: str = "fcam"


class StateConfig(BaseModel):
    mode: str = "memory"  # memory | redis
    redis: RedisConfig = Field(default_factory=RedisConfig)


class ControlPlaneConfig(BaseModel):
    batch_key_test_max_workers: int = 10


class ProviderConfig(BaseModel):
    """Per-provider configuration."""
    enabled: bool = True
    base_url: str
    auth_mode: str = "bearer"  # bearer | x-api-key
    timeout: int = 30
    max_retries: int = 3
    route_prefix: str = ""
    allowed_paths: list[str] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, v: str) -> str:
        normalized = v.strip()
        if not normalized:
            raise ValueError("provider base_url must not be empty")
        return normalized.rstrip("/")


class ProvidersConfig(BaseModel):
    exa: ProviderConfig = Field(default_factory=lambda: ProviderConfig(
        enabled=False,
        base_url="https://api.exa.ai",
        auth_mode="x-api-key",
        timeout=30,
        max_retries=3,
        route_prefix="/exa",
        allowed_paths=["search", "findSimilar", "contents", "answer"],
    ))


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    firecrawl: FirecrawlConfig = Field(default_factory=FirecrawlConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    quota: QuotaConfig = Field(default_factory=QuotaConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    credit_monitoring: CreditMonitoringConfig = Field(default_factory=CreditMonitoringConfig)
    idempotency: IdempotencyConfig = Field(default_factory=IdempotencyConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    control_plane: ControlPlaneConfig = Field(default_factory=ControlPlaneConfig)


class Secrets(BaseModel):
    admin_token: str | None = None
    master_key: str | None = None


_RESERVED_ENV_KEYS = {"FCAM_CONFIG", "FCAM_ADMIN_TOKEN", "FCAM_MASTER_KEY"}


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError("config.yaml 顶层必须是 object/dict")
    return data


def _env_overrides(prefix: str = "FCAM_", nested_delimiter: str = "__") -> dict[str, Any]:
    result: dict[str, Any] = {}

    for key, raw_value in os.environ.items():
        if key in _RESERVED_ENV_KEYS:
            continue
        if key == "FCAM_DATABASE_URL":
            # Backward/compat alias: allow a single underscore variant to override database.url.
            # Prefer the nested form if both are present.
            if "FCAM_DATABASE__URL" in os.environ:
                continue
            key = "FCAM_DATABASE__URL"
        if not key.startswith(prefix):
            continue

        path = key[len(prefix) :].lower().split(nested_delimiter)
        value = yaml.safe_load(raw_value)

        cursor: dict[str, Any] = result
        for part in path[:-1]:
            next_cursor = cursor.get(part)
            if not isinstance(next_cursor, dict):
                next_cursor = {}
                cursor[part] = next_cursor
            cursor = next_cursor
        cursor[path[-1]] = value

    return result


def load_config() -> tuple[AppConfig, Secrets]:
    defaults = AppConfig().model_dump()

    env_db_url = os.environ.get("FCAM_DATABASE_URL")
    env_db_nested_url = os.environ.get("FCAM_DATABASE__URL")
    if env_db_url and env_db_nested_url and env_db_url != env_db_nested_url:
        raise ValueError(
            "FCAM_DATABASE_URL 与 FCAM_DATABASE__URL 同时设置但不一致；请只设置一个或保持一致。"
        )

    config_path = Path(os.environ.get("FCAM_CONFIG", "config.yaml"))
    yaml_config = _load_yaml_file(config_path)
    env_config = _env_overrides()

    merged = _deep_merge(defaults, yaml_config)
    merged = _deep_merge(merged, env_config)

    config = AppConfig.model_validate(merged)

    admin_token = os.environ.get(config.security.admin.token_env)
    master_key = os.environ.get(config.security.key_encryption.master_key_env)
    secrets = Secrets(admin_token=admin_token, master_key=master_key)

    return config, secrets
