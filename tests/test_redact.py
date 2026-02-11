from __future__ import annotations

from app.core.redact import REDACTED, redact_data, redact_text


def test_redact_text_masks_bearer_and_firecrawl_key():
    raw = "Authorization: Bearer abc.def_123 fc-1234567890abcdef"
    out = redact_text(raw)
    assert "abc.def_123" not in out
    assert "fc-1234567890abcdef" not in out
    assert REDACTED in out


def test_redact_data_masks_sensitive_keys_and_strings():
    raw = {
        "authorization": "Bearer abc.def_123",
        "nested": {"token": "Bearer zzz.yyy.xxx"},
        "list": [{"api_key": "fc-1234567890abcdef"}],
        "ok": "hello",
    }
    out = redact_data(raw, {"authorization", "token", "api_key"})
    assert out["authorization"] == REDACTED
    assert out["nested"]["token"] == REDACTED
    assert out["list"][0]["api_key"] == REDACTED
    assert out["ok"] == "hello"

