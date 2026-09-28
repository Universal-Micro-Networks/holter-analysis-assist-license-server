from typing import Any

from license_server.domain.types import License, Period, UsageLogEntry, UsageSummary

PERIOD_TIMEZONE = "Asia/Tokyo"


def license_dto(license_: License) -> dict[str, Any]:
    return {
        "license_key": license_.license_key,
        "monthly_limit": license_.monthly_limit,
        "status": str(license_.status),
        "created_at": license_.created_at,
        "updated_at": license_.updated_at,
    }


def period_dto(period: Period) -> dict[str, Any]:
    return {"start": period.start_utc, "end": period.end_utc, "timezone": PERIOD_TIMEZONE}


def usage_summary_dto(summary: UsageSummary) -> dict[str, Any]:
    return {
        "used": summary.used,
        "monthly_limit": summary.monthly_limit,
        "remaining": summary.remaining,
        "period": period_dto(summary.period),
    }


def usage_entry_dto(entry: UsageLogEntry) -> dict[str, Any]:
    return {"id": entry.id, "used_at": entry.used_at}
