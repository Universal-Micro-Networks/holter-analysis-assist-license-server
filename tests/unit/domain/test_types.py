import dataclasses

import pytest

from license_server.domain.types import License, LicenseStatus, Period, UsageLogEntry, UsageSummary

PERIOD = Period(start_utc="2026-08-31T15:00:00.000Z", end_utc="2026-09-30T15:00:00.000Z")


def test_license_status_values_match_database_values() -> None:
    assert [status.value for status in LicenseStatus] == ["active", "suspended"]
    assert LicenseStatus("suspended") is LicenseStatus.SUSPENDED


def test_license_is_immutable() -> None:
    license_ = License(
        license_key="lk_" + "0" * 32,
        monthly_limit=100,
        status=LicenseStatus.ACTIVE,
        created_at="2026-09-28T00:00:00.000Z",
        updated_at="2026-09-28T00:00:00.000Z",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        license_.monthly_limit = 200  # type: ignore[misc]


def test_period_rejects_empty_or_reversed_range() -> None:
    with pytest.raises(ValueError):
        Period(start_utc=PERIOD.end_utc, end_utc=PERIOD.start_utc)
    with pytest.raises(ValueError):
        Period(start_utc=PERIOD.start_utc, end_utc=PERIOD.start_utc)


def test_usage_summary_computes_remaining() -> None:
    summary = UsageSummary.of(used=30, monthly_limit=100, period=PERIOD)
    assert (summary.used, summary.monthly_limit, summary.remaining) == (30, 100, 70)
    assert summary.period == PERIOD


def test_usage_summary_remaining_never_negative_after_limit_is_lowered() -> None:
    summary = UsageSummary.of(used=120, monthly_limit=100, period=PERIOD)
    assert summary.remaining == 0


def test_usage_log_entry_holds_id_and_timestamp_only() -> None:
    entry = UsageLogEntry(id=1, used_at="2026-09-28T00:00:00.000Z")
    assert [field.name for field in dataclasses.fields(entry)] == ["id", "used_at"]
