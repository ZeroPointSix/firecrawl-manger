from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.config import LoggingConfig
from app.core.redact import redact_data, redact_text

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def get_request_id() -> str | None:
    return request_id_ctx.get()


class JsonFormatter(logging.Formatter):
    def __init__(self, sensitive_keys: set[str]):
        super().__init__()
        self._sensitive_keys = sensitive_keys

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "request_id": get_request_id() or "-",
        }

        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload["fields"] = redact_data(fields, self._sensitive_keys)

        return json.dumps(payload, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    def __init__(self, sensitive_keys: set[str]):
        super().__init__()
        self._sensitive_keys = sensitive_keys

    def format(self, record: logging.LogRecord) -> str:
        request_id = get_request_id() or "-"
        message = redact_text(record.getMessage())

        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            safe_fields = redact_data(fields, self._sensitive_keys)
            return (
                f"{record.levelname} {record.name} request_id={request_id} "
                f"{message} {json.dumps(safe_fields, ensure_ascii=False)}"
            )

        return f"{record.levelname} {record.name} request_id={request_id} {message}"


def configure_logging(config: LoggingConfig) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(config.level.upper())

    sensitive_keys = {k.lower() for k in config.redact_fields}
    handler = logging.StreamHandler(sys.stdout)
    if config.format == "plain":
        handler.setFormatter(PlainFormatter(sensitive_keys))
    else:
        handler.setFormatter(JsonFormatter(sensitive_keys))

    root.addHandler(handler)
