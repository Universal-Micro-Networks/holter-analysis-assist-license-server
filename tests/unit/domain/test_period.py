from datetime import UTC, datetime, timedelta, timezone

import pytest

from license_server.domain.period import (
    month_period,
    month_period_of,
    parse_month,
    parse_timestamp,
    to_utc_text,
)
from license_server.domain.types import Period


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_to_utc_text_is_fixed_width_with_milliseconds() -> None:
    assert to_utc_text(utc(2026, 9, 1, 3, 4, 5, 678_901)) == "2026-09-01T03:04:05.678Z"
    assert to_utc_text(utc(2026, 9, 1)) == "2026-09-01T00:00:00.000Z"


def test_to_utc_text_converts_other_offsets_to_utc() -> None:
    jst = timezone(timedelta(hours=9))
    assert to_utc_text(datetime(2026, 9, 1, 9, 0, tzinfo=jst)) == "2026-09-01T00:00:00.000Z"


def test_to_utc_text_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        to_utc_text(datetime(2026, 9, 1))


def test_text_order_matches_time_order() -> None:
    earlier, later = utc(2026, 9, 1, 9, 59, 59, 999_000), utc(2026, 9, 1, 10, 0, 0)
    assert to_utc_text(earlier) < to_utc_text(later)


def test_month_period_of_uses_jst_calendar_month() -> None:
    assert month_period_of(2026, 9) == Period("2026-08-31T15:00:00.000Z", "2026-09-30T15:00:00.000Z")


def test_month_period_of_december_rolls_over_year() -> None:
    assert month_period_of(2026, 12) == Period("2026-11-30T15:00:00.000Z", "2026-12-31T15:00:00.000Z")


@pytest.mark.parametrize("month", [0, 13])
def test_month_period_of_rejects_invalid_month(month: int) -> None:
    with pytest.raises(ValueError):
        month_period_of(2026, month)


def test_month_period_at_jst_month_start_is_the_new_month() -> None:
    # 2026-10-01 00:00 JST is still September 30 in UTC.
    assert month_period(utc(2026, 9, 30, 15, 0)) == month_period_of(2026, 10)


def test_month_period_at_last_moment_of_jst_month_is_that_month() -> None:
    assert month_period(utc(2026, 9, 30, 14, 59, 59, 999_000)) == month_period_of(2026, 9)


def test_month_period_across_new_year_in_jst() -> None:
    assert month_period(utc(2026, 12, 31, 15, 0)) == month_period_of(2027, 1)
    assert month_period(utc(2026, 12, 31, 14, 59, 59)) == month_period_of(2026, 12)


def test_period_contains_boundaries_half_open() -> None:
    period = month_period_of(2026, 9)
    assert period.start_utc <= to_utc_text(utc(2026, 8, 31, 15, 0)) < period.end_utc
    assert not to_utc_text(utc(2026, 9, 30, 15, 0)) < period.end_utc


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-01T09:00:00+09:00", "2026-09-01T00:00:00.000Z"),
        ("2026-09-01T00:00:00Z", "2026-09-01T00:00:00.000Z"),
        ("2026-09-01T00:00:00.123456+00:00", "2026-09-01T00:00:00.123Z"),
    ],
)
def test_parse_timestamp_normalizes_to_utc_text(value: str, expected: str) -> None:
    assert parse_timestamp(value) == expected


@pytest.mark.parametrize("value", ["2026-09-01T00:00:00", "2026-09-01", "yesterday", ""])
def test_parse_timestamp_requires_timezone_and_valid_format(value: str) -> None:
    with pytest.raises(ValueError):
        parse_timestamp(value)


def test_parse_month_returns_year_and_month() -> None:
    assert parse_month("2026-09") == (2026, 9)


@pytest.mark.parametrize("value", ["2026-9", "2026-13", "2026-00", "202609", "2026-09-01", ""])
def test_parse_month_rejects_other_formats(value: str) -> None:
    with pytest.raises(ValueError):
        parse_month(value)
