import itertools

import pytest

from license_server.domain.period import month_period_of
from license_server.domain.types import (
    AuditAction,
    AuditContext,
    License,
    LicenseSearch,
    LicenseSort,
    LicenseStatus,
    Operator,
    SortOrder,
)
from license_server.repository.base import (
    ConsoleRepository,
    DuplicateLicenseKey,
    DuplicateSubmission,
    RepositoryUnavailable,
)
from license_server.repository.d1 import D1Repository
from tests.fakes.fake_d1 import FakeD1Binding
from tests.fakes.sqlite_repository import SqliteRepository

SEPTEMBER = month_period_of(2026, 9)
OPERATOR = Operator(email="ops@example.com", subject="sub-1")
_request_ids = itertools.count(1)


def key(char: str) -> str:
    return "lk_" + char * 32


def ctx(request_id: str | None = None, now: str = "2026-09-15T03:00:00.000Z", ip: str | None = "203.0.113.5") -> AuditContext:
    return AuditContext(OPERATOR, request_id or f"req-{next(_request_ids)}", ip, now)


def search(
    repo: SqliteRepository,
    query: str | None = None,
    status: LicenseStatus | None = None,
    limit: int = 51,
    offset: int = 0,
    sort: LicenseSort = LicenseSort.CREATED_AT,
    order: SortOrder = SortOrder.DESC,
) -> list[str]:
    items = repo.search(LicenseSearch(query, status, sort=sort, order=order), SEPTEMBER, limit, offset)
    return [item.license.license_key for item in items]


class D1Harness(D1Repository):
    """D1Repository on the fake binding, with the same inspection hooks as the SQLite fake."""

    def __init__(self) -> None:
        self.binding = FakeD1Binding()
        super().__init__(self.binding, run_sync=lambda value: value)

    @property
    def unavailable(self) -> bool:
        return self.binding.fail_with is not None

    @unavailable.setter
    def unavailable(self, value: bool) -> None:
        self.binding.fail_with = "D1_ERROR: Network connection lost." if value else None

    def audit_source_ips(self, license_key: str) -> list[str | None]:
        rows = self.binding.prepare("SELECT source_ip FROM console_audit_logs WHERE license_key = ?1 ORDER BY id")
        return [row["source_ip"] for row in rows.bind(license_key).all()["results"]]


@pytest.fixture(params=["sqlite", "d1"])
def repo(request: pytest.FixtureRequest) -> SqliteRepository:
    return SqliteRepository() if request.param == "sqlite" else D1Harness()  # type: ignore[return-value]


def issue(repo: SqliteRepository, char: str, memo: str = "", created: str = "2026-09-01T00:00:00.000Z", limit: int = 10) -> License:
    return repo.create_audited(key(char), limit, memo, ctx(now=created))


def test_fake_satisfies_console_repository_protocol(repo: SqliteRepository) -> None:
    assert isinstance(repo, ConsoleRepository)


class TestSearch:
    def test_lists_newest_first_then_by_key(self, repo: SqliteRepository) -> None:
        issue(repo, "b", created="2026-09-01T00:00:00.000Z")
        issue(repo, "a", created="2026-09-01T00:00:00.000Z")
        issue(repo, "c", created="2026-09-02T00:00:00.000Z")
        assert search(repo) == [key("c"), key("a"), key("b")]

    def test_matches_japanese_memo_substring(self, repo: SqliteRepository) -> None:
        issue(repo, "a", memo="山田病院 循環器内科")
        issue(repo, "b", memo="佐藤クリニック")
        assert search(repo, "循環器") == [key("a")]

    def test_memo_match_ignores_ascii_case(self, repo: SqliteRepository) -> None:
        issue(repo, "a", memo="Tokyo Heart Center")
        assert search(repo, "heart CENTER") == [key("a")]

    def test_matches_license_key_substring_in_any_case(self, repo: SqliteRepository) -> None:
        issue(repo, "a")
        issue(repo, "b")
        assert search(repo, "BBBB") == [key("b")]
        assert search(repo, key("a")) == [key("a")]

    def test_query_longer_than_50_bytes_works_without_like(self, repo: SqliteRepository) -> None:
        issue(repo, "a", memo="あ" * 30)
        assert search(repo, "あ" * 20) == [key("a")]

    def test_like_wildcards_are_literal(self, repo: SqliteRepository) -> None:
        issue(repo, "a", memo="100% 契約")
        issue(repo, "b", memo="契約_予定")
        issue(repo, "c", memo="契約X予定")
        assert search(repo, "%") == [key("a")]
        assert search(repo, "約_予") == [key("b")]

    def test_filters_by_status(self, repo: SqliteRepository) -> None:
        issue(repo, "a")
        issue(repo, "b")
        repo.set_status_audited(key("b"), LicenseStatus.SUSPENDED, ctx())
        assert search(repo, status=LicenseStatus.SUSPENDED) == [key("b")]
        assert search(repo, status=LicenseStatus.ACTIVE) == [key("a")]

    def test_query_and_status_combine(self, repo: SqliteRepository) -> None:
        issue(repo, "a", memo="病院")
        issue(repo, "b", memo="病院")
        repo.set_status_audited(key("b"), LicenseStatus.SUSPENDED, ctx())
        assert search(repo, "病院", LicenseStatus.ACTIVE) == [key("a")]

    def test_no_match_returns_empty(self, repo: SqliteRepository) -> None:
        issue(repo, "a", memo="病院")
        assert search(repo, "クリニック") == []

    def test_limit_and_offset_page_through_results(self, repo: SqliteRepository) -> None:
        for index, char in enumerate("abcde"):
            issue(repo, char, created=f"2026-09-0{index + 1}T00:00:00.000Z")
        assert search(repo, limit=2, offset=0) == [key("e"), key("d")]
        assert search(repo, limit=2, offset=4) == [key("a")]

    def test_sorts_by_monthly_limit_with_newest_first_on_ties(self, repo: SqliteRepository) -> None:
        issue(repo, "a", limit=5, created="2026-09-01T00:00:00.000Z")
        issue(repo, "b", limit=100, created="2026-09-02T00:00:00.000Z")
        issue(repo, "c", limit=5, created="2026-09-03T00:00:00.000Z")
        assert search(repo, sort=LicenseSort.MONTHLY_LIMIT, order=SortOrder.ASC) == [key("c"), key("a"), key("b")]
        assert search(repo, sort=LicenseSort.MONTHLY_LIMIT, order=SortOrder.DESC) == [key("b"), key("c"), key("a")]

    def test_sorts_by_usage_this_month(self, repo: SqliteRepository) -> None:
        issue(repo, "a", created="2026-09-01T00:00:00.000Z")
        issue(repo, "b", created="2026-09-02T00:00:00.000Z")
        issue(repo, "c", created="2026-09-03T00:00:00.000Z")
        for used_at in ["2026-09-10T00:00:00.000Z", "2026-09-11T00:00:00.000Z"]:
            repo.try_insert_within_limit(key("a"), used_at, SEPTEMBER)
        repo.try_insert_within_limit(key("b"), "2026-09-10T00:00:00.000Z", SEPTEMBER)
        repo.try_insert_within_limit(key("c"), "2026-08-20T00:00:00.000Z", month_period_of(2026, 8))
        assert search(repo, sort=LicenseSort.USED, order=SortOrder.DESC) == [key("a"), key("b"), key("c")]
        assert search(repo, sort=LicenseSort.USED, order=SortOrder.ASC) == [key("c"), key("b"), key("a")]

    def test_sorts_by_issue_time_ascending(self, repo: SqliteRepository) -> None:
        issue(repo, "b", created="2026-09-02T00:00:00.000Z")
        issue(repo, "a", created="2026-09-01T00:00:00.000Z")
        assert search(repo, sort=LicenseSort.CREATED_AT, order=SortOrder.ASC) == [key("a"), key("b")]

    def test_sort_applies_before_paging(self, repo: SqliteRepository) -> None:
        for index, char in enumerate("abcde"):
            issue(repo, char, limit=index, created=f"2026-09-0{5 - index}T00:00:00.000Z")
        assert search(repo, limit=2, offset=2, sort=LicenseSort.MONTHLY_LIMIT, order=SortOrder.ASC) == [key("c"), key("d")]

    def test_counts_matching_licenses(self, repo: SqliteRepository) -> None:
        issue(repo, "a", memo="病院")
        issue(repo, "b", memo="病院")
        issue(repo, "c", memo="医院")
        repo.set_status_audited(key("b"), LicenseStatus.SUSPENDED, ctx())
        assert repo.count_matching(LicenseSearch(None, None)) == 3
        assert repo.count_matching(LicenseSearch("病院", None)) == 2
        assert repo.count_matching(LicenseSearch("病院", LicenseStatus.ACTIVE)) == 1
        assert repo.count_matching(LicenseSearch("存在しない", None)) == 0

    def test_counts_usage_in_the_given_month_only(self, repo: SqliteRepository) -> None:
        issue(repo, "a")
        month = month_period_of(2026, 9)
        for used_at in ["2026-09-10T00:00:00.000Z", "2026-09-11T00:00:00.000Z"]:
            repo.try_insert_within_limit(key("a"), used_at, month)
        repo.try_insert_within_limit(key("a"), "2026-10-05T00:00:00.000Z", month_period_of(2026, 10))
        [item] = repo.search(LicenseSearch(None, None), SEPTEMBER, 51, 0)
        assert item.used_this_month == 2


class TestFind:
    def test_finds_by_public_id(self, repo: SqliteRepository) -> None:
        created = issue(repo, "a")
        assert created.public_id is not None
        assert repo.find_by_public_id(created.public_id) == created
        assert repo.find_by_public_id("lic_0000000000000000") is None

    def test_finds_license_by_request_id(self, repo: SqliteRepository) -> None:
        created = repo.create_audited(key("a"), 10, "", ctx("req-issue"))
        assert repo.find_license_by_request_id("req-issue") == created
        assert repo.find_license_by_request_id("unknown") is None


class TestAuditedWrites:
    def test_issue_records_values_and_operator(self, repo: SqliteRepository) -> None:
        created = repo.create_audited(key("a"), 25, "山田病院", ctx(now="2026-09-15T03:00:00.000Z"))
        assert (created.monthly_limit, created.memo, created.status) == (25, "山田病院", LicenseStatus.ACTIVE)
        [entry] = repo.list_audits(key("a"), 50)
        assert entry.action is AuditAction.ISSUE
        assert entry.operator_email == OPERATOR.email
        assert entry.before is None
        assert entry.after == {"monthly_limit": 25, "memo": "山田病院"}
        assert entry.created_at == "2026-09-15T03:00:00.000Z"

    def test_issue_with_duplicate_key_is_reported_and_leaves_no_audit(self, repo: SqliteRepository) -> None:
        issue(repo, "a")
        with pytest.raises(DuplicateLicenseKey):
            repo.create_audited(key("a"), 5, "", ctx("req-dup-key"))
        assert repo.find_license_by_request_id("req-dup-key") is None
        assert len(repo.list_audits(key("a"), 50)) == 1

    def test_issue_with_reused_request_id_creates_nothing(self, repo: SqliteRepository) -> None:
        repo.create_audited(key("a"), 10, "", ctx("req-1"))
        with pytest.raises(DuplicateSubmission) as raised:
            repo.create_audited(key("b"), 10, "", ctx("req-1"))
        assert raised.value.request_id == "req-1"
        assert repo.find(key("b")) is None

    def test_update_limit_records_before_and_after(self, repo: SqliteRepository) -> None:
        issue(repo, "a", limit=10)
        updated = repo.update_limit_audited(key("a"), 3, ctx(now="2026-09-16T00:00:00.000Z"))
        assert updated is not None
        assert (updated.monthly_limit, updated.updated_at) == (3, "2026-09-16T00:00:00.000Z")
        latest = repo.list_audits(key("a"), 50)[0]
        assert (latest.action, latest.before, latest.after) == (AuditAction.UPDATE_LIMIT, {"monthly_limit": 10}, {"monthly_limit": 3})

    def test_update_limit_with_reused_request_id_changes_nothing(self, repo: SqliteRepository) -> None:
        issue(repo, "a", limit=10)
        repo.update_limit_audited(key("a"), 20, ctx("req-limit"))
        with pytest.raises(DuplicateSubmission):
            repo.update_limit_audited(key("a"), 30, ctx("req-limit"))
        assert repo.find(key("a")).monthly_limit == 20  # type: ignore[union-attr]
        assert len(repo.list_audits(key("a"), 50)) == 2

    def test_writes_to_missing_license_return_none_and_record_nothing(self, repo: SqliteRepository) -> None:
        assert repo.update_limit_audited(key("z"), 3, ctx("r1")) is None
        assert repo.set_status_audited(key("z"), LicenseStatus.SUSPENDED, ctx("r2")) is None
        assert repo.update_memo_audited(key("z"), "x", ctx("r3")) is None
        assert repo.list_audits(key("z"), 50) == []

    def test_status_change_records_before_and_after(self, repo: SqliteRepository) -> None:
        issue(repo, "a")
        suspended = repo.set_status_audited(key("a"), LicenseStatus.SUSPENDED, ctx())
        assert suspended is not None and suspended.status is LicenseStatus.SUSPENDED
        activated = repo.set_status_audited(key("a"), LicenseStatus.ACTIVE, ctx())
        assert activated is not None and activated.status is LicenseStatus.ACTIVE
        entries = repo.list_audits(key("a"), 50)
        assert [(e.action, e.before, e.after) for e in entries[:2]] == [
            (AuditAction.ACTIVATE, {"status": "suspended"}, {"status": "active"}),
            (AuditAction.SUSPEND, {"status": "active"}, {"status": "suspended"}),
        ]

    def test_status_change_to_current_status_changes_and_records_nothing(self, repo: SqliteRepository) -> None:
        created = issue(repo, "a")
        assert repo.set_status_audited(key("a"), LicenseStatus.ACTIVE, ctx(now="2026-09-20T00:00:00.000Z")) is None
        assert repo.find(key("a")) == created
        assert len(repo.list_audits(key("a"), 50)) == 1

    def test_update_memo_records_before_and_after(self, repo: SqliteRepository) -> None:
        issue(repo, "a", memo="旧")
        updated = repo.update_memo_audited(key("a"), "新しいメモ", ctx())
        assert updated is not None and updated.memo == "新しいメモ"
        latest = repo.list_audits(key("a"), 50)[0]
        assert (latest.action, latest.before, latest.after) == (AuditAction.UPDATE_MEMO, {"memo": "旧"}, {"memo": "新しいメモ"})

    def test_audits_are_newest_first_and_limited(self, repo: SqliteRepository) -> None:
        issue(repo, "a")
        for limit in (1, 2, 3):
            repo.update_limit_audited(key("a"), limit, ctx())
        entries = repo.list_audits(key("a"), 2)
        assert [e.after for e in entries] == [{"monthly_limit": 3}, {"monthly_limit": 2}]

    def test_sign_in_is_recorded_without_license(self, repo: SqliteRepository) -> None:
        repo.record_sign_in(ctx("req-sign-in", ip=None))
        with pytest.raises(DuplicateSubmission):
            repo.record_sign_in(ctx("req-sign-in"))

    def test_audit_keeps_source_ip(self, repo: SqliteRepository) -> None:
        repo.create_audited(key("a"), 1, "", ctx(ip="198.51.100.7"))
        assert repo.audit_source_ips(key("a")) == ["198.51.100.7"]

    def test_outage_is_reported_for_console_operations(self, repo: SqliteRepository) -> None:
        repo.unavailable = True
        with pytest.raises(RepositoryUnavailable):
            repo.search(LicenseSearch(None, None), SEPTEMBER, 51, 0)
        with pytest.raises(RepositoryUnavailable):
            repo.create_audited(key("a"), 1, "", ctx())
