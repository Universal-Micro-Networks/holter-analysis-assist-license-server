import re
from datetime import UTC, date, datetime, timedelta, timezone

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


def format_jst(utc_text: str) -> str:
    moment = datetime.fromisoformat(utc_text).astimezone(JST)
    return f"{moment:%Y-%m-%d %H:%M:%S}"


def jst_date_range(start: date, end: date) -> Period:
    """Both days inclusive in JST, as the half-open UTC range [start 00:00 JST, day after end 00:00 JST)."""
    if start > end:
        raise ValueError(f"start date must not be after end date: {start} > {end}")
    begin = datetime(start.year, start.month, start.day, tzinfo=JST)
    finish = datetime(end.year, end.month, end.day, tzinfo=JST) + timedelta(days=1)
    return Period(start_utc=to_utc_text(begin), end_utc=to_utc_text(finish))


def jst_month_bounds(now_utc: datetime) -> tuple[date, date]:
    """First and last JST calendar day of the month containing `now_utc`."""
    local = now_utc.astimezone(JST)
    first = date(local.year, local.month, 1)
    next_first = date(local.year + 1, 1, 1) if local.month == 12 else date(local.year, local.month + 1, 1)
    return first, next_first - timedelta(days=1)


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
