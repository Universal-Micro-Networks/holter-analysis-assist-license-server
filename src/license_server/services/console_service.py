import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from license_server.domain.errors import ErrorCode, ServiceError
from license_server.domain.license_key import generate_license_key
from license_server.domain.period import jst_month_bounds, month_period, to_utc_text
from license_server.domain.types import (
    AuditContext,
    AuditEntry,
    License,
    LicenseListItem,
    LicenseSearch,
    LicenseStatus,
    Operator,
    Period,
    UsageLogEntry,
    UsageSummary,
)
from license_server.repository.base import (
    ConsoleRepository,
    DuplicateLicenseKey,
    RepositoryUnavailable,
)
from license_server.services.common import Clock, ensure_exists
from license_server.services.license_service import validate_monthly_limit
from license_server.services.usage_service import UsageService

MAX_MEMO_LENGTH = 200
MAX_QUERY_LENGTH = 100
LICENSE_PAGE_SIZE = 20
USAGE_PAGE_SIZE = 100
AUDIT_DISPLAY_LIMIT = 50
_ISSUE_ATTEMPTS = 3

# Bidirectional overrides can make a memo display differently from what is stored.
_BIDI_CONTROLS = {chr(c) for c in [*range(0x202A, 0x202F), *range(0x2066, 0x206A)]}


class MemoProblem(StrEnum):
    TOO_LONG = "too_long"
    CONTROL_CHARACTER = "control_character"


def memo_problem(value: str) -> MemoProblem | None:
    if len(value) > MAX_MEMO_LENGTH:
        return MemoProblem.TOO_LONG
    if any(unicodedata.category(char) in {"Cc", "Zl", "Zp"} or char in _BIDI_CONTROLS for char in value):
        return MemoProblem.CONTROL_CHARACTER
    return None


def validate_memo(value: object) -> str:
    if not isinstance(value, str):
        raise ServiceError(ErrorCode.INVALID_REQUEST, "memo must be a string.")
    memo = value.strip()
    if memo_problem(memo) is not None:
        raise ServiceError(
            ErrorCode.INVALID_REQUEST,
            f"memo must be at most {MAX_MEMO_LENGTH} characters without line breaks or control characters.",
        )
    return memo


@dataclass(frozen=True)
class LicensePage:
    items: list[LicenseListItem]
    page: int
    total: int
    page_count: int  # at least 1, even when nothing matches

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count

    @property
    def first_number(self) -> int:
        return (self.page - 1) * LICENSE_PAGE_SIZE + 1 if self.items else 0

    @property
    def last_number(self) -> int:
        return (self.page - 1) * LICENSE_PAGE_SIZE + len(self.items)


@dataclass(frozen=True)
class LicenseDetail:
    license: License
    summary: UsageSummary
    audits: list[AuditEntry]


class ConsoleService:
    """Console operations. Every change goes through ConsoleRepository so it is audited atomically."""

    def __init__(
        self,
        repo: ConsoleRepository,
        usage: UsageService,
        clock: Clock,
        key_generator: Callable[[], str] = generate_license_key,
    ) -> None:
        self._repo = repo
        self._usage = usage
        self._clock = clock
        self._key_generator = key_generator

    def context(self, operator: Operator, request_id: str, source_ip: str | None) -> AuditContext:
        return AuditContext(operator, request_id, source_ip, to_utc_text(self._clock()))

    def search(self, criteria: LicenseSearch) -> LicensePage:
        if criteria.query is not None and len(criteria.query) > MAX_QUERY_LENGTH:
            raise ServiceError(ErrorCode.INVALID_REQUEST, f"query must be at most {MAX_QUERY_LENGTH} characters.")
        total = self._repo.count_matching(criteria)
        page_count = max(1, -(-total // LICENSE_PAGE_SIZE))
        page = min(max(criteria.page, 1), page_count)
        period = month_period(self._clock())
        rows = self._repo.search(criteria, period, LICENSE_PAGE_SIZE, (page - 1) * LICENSE_PAGE_SIZE)
        return LicensePage(items=rows, page=page, total=total, page_count=page_count)

    def license(self, public_id: str) -> License:
        return self._find(public_id)

    def detail(self, public_id: str, month: tuple[int, int] | None) -> LicenseDetail:
        license_ = self._find(public_id)
        if month is None:
            first_day, _ = jst_month_bounds(self._clock())
            month = (first_day.year, first_day.month)
        summary = self._usage.monthly_count(license_.license_key, *month)
        return LicenseDetail(license_, summary, self._repo.list_audits(license_.license_key, AUDIT_DISPLAY_LIMIT))

    def issue(self, monthly_limit: object, memo: object, ctx: AuditContext) -> License:
        limit, text = validate_monthly_limit(monthly_limit), validate_memo(memo)
        for _ in range(_ISSUE_ATTEMPTS):
            try:
                return self._repo.create_audited(self._key_generator(), limit, text, ctx)
            except DuplicateLicenseKey:
                continue
        raise RepositoryUnavailable(f"could not generate a unique license key in {_ISSUE_ATTEMPTS} attempts")

    def update_limit(self, public_id: str, monthly_limit: object, ctx: AuditContext) -> License:
        license_ = self._find(public_id)
        limit = validate_monthly_limit(monthly_limit)
        return ensure_exists(self._repo.update_limit_audited(license_.license_key, limit, ctx))

    def suspend(self, public_id: str, ctx: AuditContext) -> License:
        return self._set_status(public_id, LicenseStatus.SUSPENDED, ctx)

    def activate(self, public_id: str, ctx: AuditContext) -> License:
        return self._set_status(public_id, LicenseStatus.ACTIVE, ctx)

    def update_memo(self, public_id: str, memo: object, ctx: AuditContext) -> License:
        license_ = self._find(public_id)
        text = validate_memo(memo)
        return ensure_exists(self._repo.update_memo_audited(license_.license_key, text, ctx))

    def usage_logs(
        self, public_id: str, period: Period, after_id: int | None
    ) -> tuple[License, list[UsageLogEntry], int | None]:
        license_ = self._find(public_id)
        entries, next_after = self._usage.list_logs(
            license_.license_key, period.start_utc, period.end_utc, after_id, USAGE_PAGE_SIZE
        )
        return license_, entries, next_after

    def record_sign_in(self, ctx: AuditContext) -> None:
        self._repo.record_sign_in(ctx)

    def license_for_request(self, request_id: str) -> License | None:
        return self._repo.find_license_by_request_id(request_id)

    def _set_status(self, public_id: str, status: LicenseStatus, ctx: AuditContext) -> License:
        license_ = self._find(public_id)
        if license_.status is status:
            return license_
        changed = self._repo.set_status_audited(license_.license_key, status, ctx)
        # None here means another request made the same change first; show the current state.
        return changed if changed is not None else self._find(public_id)

    def _find(self, public_id: str) -> License:
        return ensure_exists(self._repo.find_by_public_id(public_id))
