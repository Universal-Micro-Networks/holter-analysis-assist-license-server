import pytest

from license_server.domain.errors import ErrorCode, ServiceError
from license_server.domain.period import month_period_of
from license_server.domain.types import LicenseStatus, UsageSummary
from license_server.services.usage_service import UsageService
from tests.conftest import FixedClock
from tests.fakes.sqlite_repository import SqliteRepository

KEY = "lk_" + "a" * 32
UNKNOWN = "lk_" + "f" * 32
SEPTEMBER = month_period_of(2026, 9)


@pytest.fixture
def service(repo: SqliteRepository, clock: FixedClock) -> UsageService:
    return UsageService(repo, repo, clock)


@pytest.fixture
def licensed(repo: SqliteRepository) -> SqliteRepository:
    repo.create(KEY, 3, "2026-09-01T00:00:00.000Z")
    return repo


def error_code(call) -> ErrorCode:
    with pytest.raises(ServiceError) as caught:
        call()
    return caught.value.code


class TestRecordUsage:
    def test_records_and_returns_summary(self, service: UsageService, licensed: SqliteRepository) -> None:
        assert service.record_usage(KEY) == UsageSummary(used=1, monthly_limit=3, remaining=2, period=SEPTEMBER)

    def test_uses_server_clock_for_timestamp(self, service: UsageService, licensed: SqliteRepository) -> None:
        service.record_usage(KEY)
        [entry] = licensed.list_in_range(KEY, SEPTEMBER.start_utc, SEPTEMBER.end_utc, None, 10)
        assert entry.used_at == "2026-09-15T03:00:00.000Z"

    def test_limit_n_allows_n_then_rejects(self, service: UsageService, licensed: SqliteRepository) -> None:
        remaining = [service.record_usage(KEY).remaining for _ in range(3)]
        assert remaining == [2, 1, 0]
        assert error_code(lambda: service.record_usage(KEY)) is ErrorCode.MONTHLY_LIMIT_REACHED
        assert licensed.count_in_period(KEY, SEPTEMBER) == 3

    def test_zero_limit_records_without_limit(self, service: UsageService, repo: SqliteRepository) -> None:
        repo.create(KEY, 0, "2026-09-01T00:00:00.000Z")
        summaries = [service.record_usage(KEY) for _ in range(5)]
        assert summaries[-1] == UsageSummary(used=5, monthly_limit=0, remaining=None, period=SEPTEMBER)

    def test_unknown_license_is_invalid(self, service: UsageService) -> None:
        assert error_code(lambda: service.record_usage(UNKNOWN)) is ErrorCode.LICENSE_INVALID

    def test_suspended_license_is_rejected_without_recording(
        self, service: UsageService, licensed: SqliteRepository
    ) -> None:
        licensed.set_status(KEY, LicenseStatus.SUSPENDED, "2026-09-02T00:00:00.000Z")
        assert error_code(lambda: service.record_usage(KEY)) is ErrorCode.LICENSE_SUSPENDED
        assert licensed.count_in_period(KEY, SEPTEMBER) == 0

    def test_count_resets_in_new_jst_month(
        self, service: UsageService, licensed: SqliteRepository, clock: FixedClock
    ) -> None:
        for _ in range(3):
            service.record_usage(KEY)
        clock.now = clock.now.replace(month=9, day=30, hour=15)  # 2026-10-01 00:00 JST
        summary = service.record_usage(KEY)
        assert (summary.used, summary.period) == (1, month_period_of(2026, 10))

    def test_raised_limit_applies_immediately(self, service: UsageService, licensed: SqliteRepository) -> None:
        for _ in range(3):
            service.record_usage(KEY)
        licensed.update_limit(KEY, 4, "2026-09-15T00:00:00.000Z")
        assert service.record_usage(KEY).used == 4


class TestCurrentSummary:
    def test_returns_summary_without_recording(self, service: UsageService, licensed: SqliteRepository) -> None:
        service.record_usage(KEY)
        assert service.current_summary(KEY) == UsageSummary(used=1, monthly_limit=3, remaining=2, period=SEPTEMBER)
        assert service.current_summary(KEY).used == 1

    def test_unknown_license_is_invalid(self, service: UsageService) -> None:
        assert error_code(lambda: service.current_summary(UNKNOWN)) is ErrorCode.LICENSE_INVALID

    def test_suspended_license_is_suspended(self, service: UsageService, licensed: SqliteRepository) -> None:
        licensed.set_status(KEY, LicenseStatus.SUSPENDED, "2026-09-02T00:00:00.000Z")
        assert error_code(lambda: service.current_summary(KEY)) is ErrorCode.LICENSE_SUSPENDED


class TestAdminQueries:
    def test_monthly_count_for_given_month(
        self, service: UsageService, licensed: SqliteRepository, clock: FixedClock
    ) -> None:
        service.record_usage(KEY)
        clock.now = clock.now.replace(month=10)
        service.record_usage(KEY)
        assert service.monthly_count(KEY, 2026, 9) == UsageSummary(1, 3, 2, SEPTEMBER)

    def test_monthly_count_works_for_suspended_license(
        self, service: UsageService, licensed: SqliteRepository
    ) -> None:
        service.record_usage(KEY)
        licensed.set_status(KEY, LicenseStatus.SUSPENDED, "2026-09-16T00:00:00.000Z")
        assert service.monthly_count(KEY, 2026, 9).used == 1

    def test_monthly_count_of_unknown_license_is_not_found(self, service: UsageService) -> None:
        assert error_code(lambda: service.monthly_count(UNKNOWN, 2026, 9)) is ErrorCode.LICENSE_NOT_FOUND

    def test_list_logs_pages_with_cursor(
        self, service: UsageService, licensed: SqliteRepository, clock: FixedClock
    ) -> None:
        licensed.update_limit(KEY, 10, "2026-09-01T00:00:00.000Z")
        for _ in range(5):
            service.record_usage(KEY)
            clock.advance(minutes=1)
        first, cursor = service.list_logs(KEY, SEPTEMBER.start_utc, SEPTEMBER.end_utc, None, 2)
        second, cursor2 = service.list_logs(KEY, SEPTEMBER.start_utc, SEPTEMBER.end_utc, cursor, 2)
        last, cursor3 = service.list_logs(KEY, SEPTEMBER.start_utc, SEPTEMBER.end_utc, cursor2, 2)
        assert [e.id for e in first + second + last] == [1, 2, 3, 4, 5]
        assert (cursor, cursor2, cursor3) == (2, 4, None)

    def test_list_logs_returns_no_cursor_when_page_is_exactly_full(
        self, service: UsageService, licensed: SqliteRepository
    ) -> None:
        service.record_usage(KEY)
        service.record_usage(KEY)
        entries, cursor = service.list_logs(KEY, SEPTEMBER.start_utc, SEPTEMBER.end_utc, None, 2)
        assert (len(entries), cursor) == (2, None)

    @pytest.mark.parametrize("limit", [0, 1001, -1])
    def test_list_logs_rejects_page_size_out_of_range(
        self, service: UsageService, licensed: SqliteRepository, limit: int
    ) -> None:
        call = lambda: service.list_logs(KEY, SEPTEMBER.start_utc, SEPTEMBER.end_utc, None, limit)  # noqa: E731
        assert error_code(call) is ErrorCode.INVALID_REQUEST

    def test_list_logs_rejects_reversed_range(self, service: UsageService, licensed: SqliteRepository) -> None:
        call = lambda: service.list_logs(KEY, SEPTEMBER.end_utc, SEPTEMBER.start_utc, None, 10)  # noqa: E731
        assert error_code(call) is ErrorCode.INVALID_REQUEST

    def test_list_logs_of_unknown_license_is_not_found(self, service: UsageService) -> None:
        call = lambda: service.list_logs(UNKNOWN, SEPTEMBER.start_utc, SEPTEMBER.end_utc, None, 10)  # noqa: E731
        assert error_code(call) is ErrorCode.LICENSE_NOT_FOUND
