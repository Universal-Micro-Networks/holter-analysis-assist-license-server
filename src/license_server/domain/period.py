import re
from datetime import UTC, datetime, timedelta, timezone

from license_server.domain.types import Period

# Japan has no daylight saving time, so a fixed offset avoids depending on tzdata inside Pyodide.
JST = timezone(timedelta(hours=9), "Asia/Tokyo")

_MONTH_PATTERN = re.compile(r"(\d{4})-(\d{2})")


def to_utc_text(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("naive datetime is not allowed; attach a timezone")
    moment = moment.astimezone(UTC)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def month_period_of(year: int, month: int) -> Period:
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 1-12: {month}")
    start = datetime(year, month, 1, tzinfo=JST)
    end = datetime(year + 1, 1, 1, tzinfo=JST) if month == 12 else datetime(year, month + 1, 1, tzinfo=JST)
    return Period(start_utc=to_utc_text(start), end_utc=to_utc_text(end))


def month_period(now_utc: datetime) -> Period:
    local = now_utc.astimezone(JST)
    return month_period_of(local.year, local.month)


def parse_timestamp(value: str) -> str:
    """Parse ISO 8601 with an explicit offset and return fixed-width UTC text."""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"timestamp must include a timezone offset: {value}")
    return to_utc_text(moment)


def parse_month(value: str) -> tuple[int, int]:
    match = _MONTH_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"month must be YYYY-MM: {value}")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 01-12: {value}")
    return year, month
