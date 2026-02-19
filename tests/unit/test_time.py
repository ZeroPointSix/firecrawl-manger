from __future__ import annotations

import pytest

from app.core.time import seconds_until_next_midnight, today_in_timezone

pytestmark = pytest.mark.unit


def test_today_in_timezone_returns_date():
    d = today_in_timezone("UTC")
    assert d.year >= 2000


def test_seconds_until_next_midnight_is_positive():
    seconds = seconds_until_next_midnight("UTC")
    assert 0 <= seconds <= 24 * 60 * 60

