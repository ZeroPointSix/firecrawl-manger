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
        default_factory=lambda: ["scrape", "crawl", "search", "agent", "map", "extract", "batch"]
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
        default_factory=lambda: ["authorization", "api_key", "token", "cookie", "set-cookie"]
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


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    firecrawl: FirecrawlConfig = Field(default_factory=FirecrawlConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    quota: QuotaConfig = Field(default_factory=QuotaConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
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
