from __future__ import annotations

import json

import pytest

from app.middleware import _dump_error_details, _infer_api_endpoint

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/scrape", "scrape"),
        ("/api/crawl", "crawl"),
        ("/api/crawl/abc123", "crawl_status"),
        ("/v1/search", "search"),
        # team/* 需要显式 endpoint，否则会退化为推断值 "team"
        ("/v2/team/credit-usage", "team"),
        ("/healthz", None),
    ],
)
def test_infer_api_endpoint(path: str, expected: str | None):
    assert _infer_api_endpoint(path) == expected


def test_dump_error_details_redacts_sensitive_fields():
    raw = {
        "authorization": "Bearer abc.def_123",
        "api_key": "fc-1234567890abcdef",
        "message": "ok",
    }
    dumped = _dump_error_details(raw)
    assert dumped is not None

    assert "abc.def_123" not in dumped
    assert "fc-1234567890abcdef" not in dumped
    assert "[REDACTED]" in dumped

    parsed = json.loads(dumped)
    assert parsed["authorization"] == "[REDACTED]"
    assert parsed["api_key"] == "[REDACTED]"


def test_dump_error_details_truncates_large_payload():
    raw = {
        "authorization": "Bearer abc.def_123",
        "api_key": "fc-1234567890abcdef",
        "message": "x" * 5000,
    }
    dumped = _dump_error_details(raw)
    assert dumped is not None

    assert "abc.def_123" not in dumped
    assert "fc-1234567890abcdef" not in dumped

    parsed = json.loads(dumped)
    assert parsed.get("truncated") is True
    assert len(dumped) <= 2000
