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
    memo: str = ""
    # URL identifier for the console, so the license key never appears in a URL.
    public_id: str | None = None


@dataclass(frozen=True)
class Period:
    """Half-open range [start_utc, end_utc) as fixed-width UTC ISO 8601 strings."""

    start_utc: str
    end_utc: str

    def __post_init__(self) -> None:
        if not self.start_utc < self.end_utc:
            raise ValueError(f"period start must precede end: {self.start_utc} >= {self.end_utc}")


UNLIMITED = 0  # a monthly_limit of 0 means usage is never refused for the limit


@dataclass(frozen=True)
class UsageSummary:
    used: int
    monthly_limit: int
    remaining: int | None  # None when unlimited
    period: Period

    @classmethod
    def of(cls, used: int, monthly_limit: int, period: Period) -> "UsageSummary":
        if monthly_limit == UNLIMITED:
            return cls(used=used, monthly_limit=monthly_limit, remaining=None, period=period)
        # Lowering monthly_limit below the current count is allowed, so remaining is clamped.
        return cls(used=used, monthly_limit=monthly_limit, remaining=max(monthly_limit - used, 0), period=period)


@dataclass(frozen=True)
class UsageLogEntry:
    id: int
    used_at: str


class AuditAction(StrEnum):
    ISSUE = "issue"
    UPDATE_LIMIT = "update_limit"
    SUSPEND = "suspend"
    ACTIVATE = "activate"
    UPDATE_MEMO = "update_memo"
    SIGN_IN = "sign_in"


@dataclass(frozen=True)
class Operator:
    email: str  # shown on screen and stored in audit records
    subject: str  # stable identity used to detect a different operator in the same browser


@dataclass(frozen=True)
class AuditContext:
    operator: Operator
    request_id: str  # issued once per form; reusing it marks a double submission
    source_ip: str | None
    now_utc: str


AuditValues = dict[str, str | int]


@dataclass(frozen=True)
class AuditEntry:
    id: int
    operator_email: str
    action: AuditAction
    before: AuditValues | None
    after: AuditValues | None
    created_at: str


class LicenseSort(StrEnum):
    CREATED_AT = "created_at"
    MONTHLY_LIMIT = "monthly_limit"
    USED = "used"  # usage in the current month


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True)
class LicenseSearch:
    query: str | None  # substring of the memo or the license key
    status: LicenseStatus | None
    page: int = 1
    sort: LicenseSort = LicenseSort.CREATED_AT
    order: SortOrder = SortOrder.DESC


@dataclass(frozen=True)
class LicenseListItem:
    license: License
    used_this_month: int
