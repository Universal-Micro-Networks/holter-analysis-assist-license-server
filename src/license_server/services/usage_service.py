from license_server.domain.errors import ErrorCode, ServiceError
from license_server.domain.period import month_period, month_period_of, to_utc_text
from license_server.domain.types import UsageLogEntry, UsageSummary
from license_server.repository.base import LicenseRepository, UsageRepository
from license_server.services.common import Clock, ensure_active, ensure_exists

MAX_PAGE_SIZE = 1000
DEFAULT_PAGE_SIZE = 100


class UsageService:
    """Sole writer of usage_logs."""

    def __init__(self, usage: UsageRepository, licenses: LicenseRepository, clock: Clock) -> None:
        self._usage = usage
        self._licenses = licenses
        self._clock = clock

    def record_usage(self, license_key: str) -> UsageSummary:
        now = self._clock()
        period = month_period(now)
        result = self._usage.try_insert_within_limit(license_key, to_utc_text(now), period)
        if result.inserted and result.monthly_limit is not None:
            return UsageSummary.of(result.used_after, result.monthly_limit, period)
        # Only the rejection path pays for a second read to explain why.
        ensure_active(self._licenses.find(license_key))
        raise ServiceError(ErrorCode.MONTHLY_LIMIT_REACHED)

    def current_summary(self, license_key: str) -> UsageSummary:
        license_ = ensure_active(self._licenses.find(license_key))
        period = month_period(self._clock())
        return UsageSummary.of(self._usage.count_in_period(license_key, period), license_.monthly_limit, period)

    def monthly_count(self, license_key: str, year: int, month: int) -> UsageSummary:
        license_ = ensure_exists(self._licenses.find(license_key))
        period = month_period_of(year, month)
        return UsageSummary.of(self._usage.count_in_period(license_key, period), license_.monthly_limit, period)

    def list_logs(
        self, license_key: str, start_utc: str, end_utc: str, after_id: int | None, limit: int
    ) -> tuple[list[UsageLogEntry], int | None]:
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ServiceError(ErrorCode.INVALID_REQUEST, f"limit must be an integer between 1 and {MAX_PAGE_SIZE}.")
        if not start_utc < end_utc:
            raise ServiceError(ErrorCode.INVALID_REQUEST, "from must be earlier than to.")
        ensure_exists(self._licenses.find(license_key))
        entries = self._usage.list_in_range(license_key, start_utc, end_utc, after_id, limit + 1)
        if len(entries) > limit:
            return entries[:limit], entries[limit - 1].id
        return entries, None
