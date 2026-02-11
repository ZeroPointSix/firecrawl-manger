from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"

_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._\-~+/]+=*\b")
_FIRECRAWL_KEY_RE = re.compile(r"\bfc-[A-Za-z0-9]{8,}\b")


def redact_text(text: str) -> str:
    redacted = _BEARER_RE.sub("Bearer " + REDACTED, text)
    return _FIRECRAWL_KEY_RE.sub("fc-" + REDACTED, redacted)


def redact_data(value: Any, sensitive_keys: set[str]) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for k, v in value.items():
            if str(k).lower() in sensitive_keys:
                redacted[k] = REDACTED
            else:
                redacted[k] = redact_data(v, sensitive_keys)
        return redacted

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_data(v, sensitive_keys) for v in value]

    if isinstance(value, str):
        return redact_text(value)

    return value
