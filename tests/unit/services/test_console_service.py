import itertools
from collections.abc import Iterator

import pytest

from license_server.domain.errors import ErrorCode, ServiceError
from license_server.domain.period import month_period_of
from license_server.domain.types import (
    AuditAction,
    AuditContext,
    LicenseSearch,
    LicenseSort,
    LicenseStatus,
    Operator,
    SortOrder,
)
from license_server.repository.base import DuplicateSubmission, RepositoryUnavailable
from license_server.services.console_service import (
    LICENSE_PAGE_SIZE,
    USAGE_PAGE_SIZE,
    ConsoleService,
    validate_memo,
)
from license_server.services.usage_service import UsageService
from tests.conftest import FixedClock
from tests.fakes.sqlite_repository import SqliteRepository

OPERATOR = Operator("ops@example.com", "sub-ops")
_ids = itertools.count(1)


def sequential_keys() -> Iterator[str]:
    for index in itertools.count():
        yield f"lk_{index:032x}"


@pytest.fixture
def service(repo: SqliteRepository, clock: FixedClock) -> ConsoleService:
    keys = sequential_keys()
    return ConsoleService(repo, UsageService(repo, repo, clock), clock, key_generator=lambda: next(keys))


def ctx(service: ConsoleService, request_id: str | None = None) -> AuditContext:
    return service.context(OPERATOR, request_id or f"req-{next(_ids)}", "203.0.113.1")


def public_id(license_: object) -> str:
    value = getattr(license_, "public_id")
    assert isinstance(value, str)
    return value


class TestIssue:
    def test_issues_with_memo_and_records_audit(self, service: ConsoleService, repo: SqliteRepository) -> None:
        issued = service.issue(10, "  山田病院 ", ctx(service))
        assert (issued.monthly_limit, issued.memo, issued.status) == (10, "山田病院", LicenseStatus.ACTIVE)
        detail = service.detail(public_id(issued), None)
        assert [a.action for a in detail.audits] == [AuditAction.ISSUE]
        assert detail.audits[0].operator_email == OPERATOR.email

    def test_context_uses_the_clock(self, service: ConsoleService, clock: FixedClock) -> None:
        assert ctx(service).now_utc == "2026-09-15T03:00:00.000Z"

    def test_regenerates_key_on_collision(self, repo: SqliteRepository, clock: FixedClock) -> None:
        keys = iter(["lk_" + "a" * 32, "lk_" + "a" * 32, "lk_" + "b" * 32])
        service = ConsoleService(repo, UsageService(repo, repo, clock), clock, key_generator=lambda: next(keys))
        service.issue(1, "", ctx(service))
        assert service.issue(1, "", ctx(service)).license_key == "lk_" + "b" * 32

    def test_gives_up_after_three_collisions(self, repo: SqliteRepository, clock: FixedClock) -> None:
        service = ConsoleService(repo, UsageService(repo, repo, clock), clock, key_generator=lambda: "lk_" + "a" * 32)
        service.issue(1, "", ctx(service))
        with pytest.raises(RepositoryUnavailable):
            service.issue(1, "", ctx(service))

    @pytest.mark.parametrize(("limit", "memo"), [(-1, ""), (1_000_001, ""), ("5", ""), (True, ""), (5, "x" * 201), (5, "a\nb"), (5, None)])
    def test_invalid_input_is_rejected(self, service: ConsoleService, limit: object, memo: object) -> None:
        with pytest.raises(ServiceError) as raised:
            service.issue(limit, memo, ctx(service))
        assert raised.value.code is ErrorCode.INVALID_REQUEST

    def test_reused_request_id_propagates_duplicate_submission(self, service: ConsoleService) -> None:
        first = service.issue(1, "", ctx(service, "req-same"))
        with pytest.raises(DuplicateSubmission):
            service.issue(1, "", ctx(service, "req-same"))
        assert service.license_for_request("req-same") == first


class TestSearch:
    def test_pages_of_20_with_total_and_page_count(self, service: ConsoleService, clock: FixedClock) -> None:
        assert LICENSE_PAGE_SIZE == 20
        for _ in range(41):
            clock.advance(seconds=1)
            service.issue(1, "", ctx(service))
        first = service.search(LicenseSearch(None, None, page=1))
        last = service.search(LicenseSearch(None, None, page=3))
        assert (len(first.items), first.page, first.total, first.page_count, first.has_next) == (20, 1, 41, 3, True)
        assert (len(last.items), last.page, last.has_next) == (1, 3, False)
        assert (first.first_number, first.last_number, last.first_number, last.last_number) == (1, 20, 41, 41)
        assert first.items[0].license.created_at > last.items[0].license.created_at

    def test_page_beyond_the_last_shows_the_last_page(self, service: ConsoleService) -> None:
        for _ in range(3):
            service.issue(1, "", ctx(service))
        page = service.search(LicenseSearch(None, None, page=9))
        assert (page.page, page.page_count, len(page.items)) == (1, 1, 3)

    def test_empty_result_has_one_empty_page(self, service: ConsoleService) -> None:
        page = service.search(LicenseSearch(None, None))
        assert (page.page, page.page_count, page.total, page.items, page.first_number, page.last_number) == (1, 1, 0, [], 0, 0)

    def test_sort_is_passed_to_the_repository(self, service: ConsoleService) -> None:
        service.issue(9, "", ctx(service))
        service.issue(1, "", ctx(service))
        page = service.search(LicenseSearch(None, None, sort=LicenseSort.MONTHLY_LIMIT, order=SortOrder.ASC))
        assert [item.license.monthly_limit for item in page.items] == [1, 9]

    def test_includes_current_month_usage(self, service: ConsoleService, repo: SqliteRepository) -> None:
        issued = service.issue(5, "", ctx(service))
        repo.try_insert_within_limit(issued.license_key, "2026-09-10T00:00:00.000Z", month_period_of(2026, 9))
        [item] = service.search(LicenseSearch(None, None)).items
        assert item.used_this_month == 1

    def test_too_long_query_is_rejected(self, service: ConsoleService) -> None:
        with pytest.raises(ServiceError):
            service.search(LicenseSearch("a" * 101, None))


class TestChanges:
    def test_update_limit(self, service: ConsoleService) -> None:
        issued = service.issue(10, "", ctx(service))
        assert service.update_limit(public_id(issued), 3, ctx(service)).monthly_limit == 3
        latest = service.detail(public_id(issued), None).audits[0]
        assert (latest.action, latest.before, latest.after) == (AuditAction.UPDATE_LIMIT, {"monthly_limit": 10}, {"monthly_limit": 3})

    def test_update_limit_rejects_invalid_value(self, service: ConsoleService) -> None:
        issued = service.issue(10, "", ctx(service))
        with pytest.raises(ServiceError):
            service.update_limit(public_id(issued), -1, ctx(service))

    def test_suspend_and_activate(self, service: ConsoleService) -> None:
        issued = service.issue(10, "", ctx(service))
        assert service.suspend(public_id(issued), ctx(service)).status is LicenseStatus.SUSPENDED
        assert service.activate(public_id(issued), ctx(service)).status is LicenseStatus.ACTIVE
        actions = [a.action for a in service.detail(public_id(issued), None).audits]
        assert actions == [AuditAction.ACTIVATE, AuditAction.SUSPEND, AuditAction.ISSUE]

    def test_status_change_without_effect_records_nothing(self, service: ConsoleService) -> None:
        issued = service.issue(10, "", ctx(service))
        assert service.activate(public_id(issued), ctx(service)) == issued
        service.suspend(public_id(issued), ctx(service))
        service.suspend(public_id(issued), ctx(service))
        actions = [a.action for a in service.detail(public_id(issued), None).audits]
        assert actions == [AuditAction.SUSPEND, AuditAction.ISSUE]

    def test_update_memo(self, service: ConsoleService) -> None:
        issued = service.issue(10, "旧", ctx(service))
        assert service.update_memo(public_id(issued), " 新 ", ctx(service)).memo == "新"

    @pytest.mark.parametrize(
        "call",
        [
            lambda s: s.detail("lic_0000000000000000", None),
            lambda s: s.update_limit("lic_0000000000000000", 1, ctx(s)),
            lambda s: s.suspend("lic_0000000000000000", ctx(s)),
            lambda s: s.activate("lic_0000000000000000", ctx(s)),
            lambda s: s.update_memo("lic_0000000000000000", "x", ctx(s)),
            lambda s: s.usage_logs("lic_0000000000000000", month_period_of(2026, 9), None),
        ],
    )
    def test_unknown_public_id_is_not_found(self, service: ConsoleService, call) -> None:
        with pytest.raises(ServiceError) as raised:
            call(service)
        assert raised.value.code is ErrorCode.LICENSE_NOT_FOUND

    def test_reused_request_id_on_change_propagates(self, service: ConsoleService) -> None:
        issued = service.issue(10, "", ctx(service))
        service.update_limit(public_id(issued), 20, ctx(service, "req-limit"))
        with pytest.raises(DuplicateSubmission):
            service.update_limit(public_id(issued), 30, ctx(service, "req-limit"))


class TestDetailAndUsage:
    def test_detail_shows_selected_month_summary(self, service: ConsoleService, repo: SqliteRepository) -> None:
        issued = service.issue(5, "", ctx(service))
        repo.try_insert_within_limit(issued.license_key, "2026-08-10T00:00:00.000Z", month_period_of(2026, 8))
        august = service.detail(public_id(issued), (2026, 8)).summary
        current = service.detail(public_id(issued), None).summary
        assert (august.used, august.remaining, august.period) == (1, 4, month_period_of(2026, 8))
        assert (current.used, current.period) == (0, month_period_of(2026, 9))

    def test_usage_logs_page_by_100_oldest_first(self, service: ConsoleService, repo: SqliteRepository) -> None:
        issued = service.issue(1000, "", ctx(service))
        september = month_period_of(2026, 9)
        for minute in range(USAGE_PAGE_SIZE + 1):
            repo.try_insert_within_limit(issued.license_key, f"2026-09-10T{minute // 60:02d}:{minute % 60:02d}:00.000Z", september)
        license_, first, next_after = service.usage_logs(public_id(issued), september, None)
        assert license_ == service.detail(public_id(issued), None).license
        assert len(first) == 100 and first[0].used_at < first[-1].used_at
        assert next_after == first[-1].id
        _, rest, end = service.usage_logs(public_id(issued), september, next_after)
        assert (len(rest), end) == (1, None)

    def test_sign_in_is_recorded(self, service: ConsoleService) -> None:
        service.record_sign_in(ctx(service, "req-sign-in"))
        with pytest.raises(DuplicateSubmission):
            service.record_sign_in(ctx(service, "req-sign-in"))


@pytest.mark.parametrize(("value", "expected"), [("  a  ", "a"), ("", ""), ("あ" * 200, "あ" * 200)])
def test_validate_memo_accepts_and_strips(value: str, expected: str) -> None:
    assert validate_memo(value) == expected
