from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re


@dataclass(frozen=True)
class ParsedKeyLine:
    line_no: int
    raw: str
    api_key: str
    account_username: str | None
    account_password: str | None
    account_verified_at: datetime | None


@dataclass(frozen=True)
class ParseFailure:
    line_no: int
    raw: str
    message: str


_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_datetime_utc(raw: str) -> datetime:
    s = (raw or "").strip()
    if not s:
        raise ValueError("empty datetime")

    if _DATE_ONLY_RE.match(s):
        s = f"{s}T00:00:00+00:00"
    elif s.endswith("Z"):
        s = s[:-1] + "+00:00"
    elif " " in s and "T" not in s and "+" not in s:
        s = s.replace(" ", "T", 1)

    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _split_fields(line: str) -> list[str]:
    if "|" in line:
        parts = line.split("|")
    elif "\t" in line:
        parts = line.split("\t")
    elif "," in line:
        parts = line.split(",")
    else:
        parts = line.split()
    return [p.strip() for p in parts]


def parse_keys_text(text: str) -> tuple[list[ParsedKeyLine], list[ParseFailure]]:
    items: list[ParsedKeyLine] = []
    failures: list[ParseFailure] = []

    for line_no, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        try:
            parts = [p for p in _split_fields(line) if p != ""]
            if len(parts) == 1:
                api_key = parts[0]
                account_username = None
                account_password = None
                verified_at = None
            elif len(parts) == 2:
                account_username, api_key = parts
                account_password = None
                verified_at = None
            elif len(parts) == 3:
                account_username, account_password, api_key = parts
                verified_at = None
            elif len(parts) == 4:
                account_username, account_password, api_key, verified_raw = parts
                verified_at = _parse_datetime_utc(verified_raw) if verified_raw else None
            else:
                raise ValueError("expect 1/2/3/4 fields per line")

            api_key = (api_key or "").strip()
            if len(api_key) < 8:
                raise ValueError("api_key too short")

            account_username = (account_username or "").strip() or None
            account_password = (account_password or "").strip() or None

            items.append(
                ParsedKeyLine(
                    line_no=line_no,
                    raw=raw,
                    api_key=api_key,
                    account_username=account_username,
                    account_password=account_password,
                    account_verified_at=verified_at,
                )
            )
        except Exception as exc:
            failures.append(ParseFailure(line_no=line_no, raw=raw, message=str(exc)))

    return items, failures
