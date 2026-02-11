from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _tzinfo(tz_name: str):
    if tz_name.upper() == "UTC":
        return timezone.utc
    return ZoneInfo(tz_name)


def today_in_timezone(tz_name: str) -> date:
    tz = _tzinfo(tz_name)
    return datetime.now(tz).date()


def seconds_until_next_midnight(tz_name: str) -> int:
    tz = _tzinfo(tz_name)
    now = datetime.now(tz)
    tomorrow = (now + timedelta(days=1)).date()
    next_midnight = datetime.combine(tomorrow, time.min, tzinfo=tz)
    seconds = int((next_midnight - now).total_seconds())
    return max(seconds, 0)
