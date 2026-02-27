from __future__ import annotations

import httpx
import pytest

from app.core.forwarder import _parse_retry_after, _strip_firecrawl_version_suffix

pytestmark = pytest.mark.unit


def test_strip_firecrawl_version_suffix_handles_invalid_url_and_v2_suffix():
    base, version = _strip_firecrawl_version_suffix("firecrawl.test/v1")
    assert base == "firecrawl.test/v1"
    assert version is None

    base2, version2 = _strip_firecrawl_version_suffix("http://firecrawl.test/v2/")
    assert base2 == "http://firecrawl.test"
    assert version2 == "v2"


def test_parse_retry_after_handles_missing_invalid_and_negative():
    assert _parse_retry_after(httpx.Headers({})) is None
    assert _parse_retry_after(httpx.Headers({"retry-after": "abc"})) is None
    assert _parse_retry_after(httpx.Headers({"retry-after": "-5"})) == 0

