from dataclasses import dataclass
from enum import StrEnum


class LicenseStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class License:
    license_key: str
    monthly_limit: int
    status: LicenseStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Period:
    """Half-open range [start_utc, end_utc) as fixed-width UTC ISO 8601 strings."""

    start_utc: str
    end_utc: str

    def __post_init__(self) -> None:
        if not self.start_utc < self.end_utc:
            raise ValueError(f"period start must precede end: {self.start_utc} >= {self.end_utc}")


@dataclass(frozen=True)
class UsageSummary:
    used: int
    monthly_limit: int
    remaining: int
    period: Period

    @classmethod
    def of(cls, used: int, monthly_limit: int, period: Period) -> "UsageSummary":
        # Lowering monthly_limit below the current count is allowed, so remaining is clamped.
        return cls(used=used, monthly_limit=monthly_limit, remaining=max(monthly_limit - used, 0), period=period)


@dataclass(frozen=True)
class UsageLogEntry:
    id: int
    used_at: str
