import pytest

from license_server.domain.period import month_period_of
from license_server.domain.types import LicenseStatus, UsageLogEntry
from license_server.repository.base import (
    DuplicateLicenseKey,
    LicenseRepository,
    RepositoryUnavailable,
    UsageRepository,
)
from tests.fakes.sqlite_repository import SqliteRepository

KEY = "lk_" + "a" * 32
OTHER = "lk_" + "b" * 32
T0 = "2026-09-01T00:00:00.000Z"
T1 = "2026-09-02T00:00:00.000Z"
SEPTEMBER = month_period_of(2026, 9)
AUGUST = month_period_of(2026, 8)


@pytest.fixture
def repo() -> SqliteRepository:
    return SqliteRepository()


def record(repo: SqliteRepository, times: list[str], key: str = KEY) -> list[bool]:
    return [repo.try_insert_within_limit(key, t, SEPTEMBER).inserted for t in times]


def minutes(count: int, day: str = "2026-09-10") -> list[str]:
    return [f"{day}T00:{m:02d}:00.000Z" for m in range(count)]


def test_fake_satisfies_both_repository_protocols(repo: SqliteRepository) -> None:
    assert isinstance(repo, LicenseRepository)
    assert isinstance(repo, UsageRepository)


class TestLicenses:
    def test_unknown_key_is_not_found(self, repo: SqliteRepository) -> None:
        assert repo.find(KEY) is None

    def test_created_license_is_active_and_findable(self, repo: SqliteRepository) -> None:
        created = repo.create(KEY, 10, T0)
        assert (created.license_key, created.monthly_limit, created.status) == (KEY, 10, LicenseStatus.ACTIVE)
        assert (created.created_at, created.updated_at) == (T0, T0)
        assert repo.find(KEY) == created

    def test_duplicate_key_is_reported(self, repo: SqliteRepository) -> None:
        repo.create(KEY, 10, T0)
        with pytest.raises(DuplicateLicenseKey):
            repo.create(KEY, 20, T1)

    def test_update_limit_changes_limit_and_updated_at(self, repo: SqliteRepository) -> None:
        repo.create(KEY, 10, T0)
        updated = repo.update_limit(KEY, 50, T1)
        assert updated is not None
        assert (updated.monthly_limit, updated.created_at, updated.updated_at) == (50, T0, T1)
        assert repo.find(KEY) == updated

    def test_update_limit_of_unknown_key_returns_none(self, repo: SqliteRepository) -> None:
        assert repo.update_limit(KEY, 50, T1) is None

    def test_set_status_toggles_between_active_and_suspended(self, repo: SqliteRepository) -> None:
        repo.create(KEY, 10, T0)
        suspended = repo.set_status(KEY, LicenseStatus.SUSPENDED, T1)
        assert suspended is not None and suspended.status is LicenseStatus.SUSPENDED
        active = repo.set_status(KEY, LicenseStatus.ACTIVE, T1)
        assert active is not None and active.status is LicenseStatus.ACTIVE

    def test_set_status_of_unknown_key_returns_none(self, repo: SqliteRepository) -> None:
        assert repo.set_status(KEY, LicenseStatus.SUSPENDED, T1) is None


class TestInsertWithinLimit:
    def test_inserts_until_limit_then_rejects(self, repo: SqliteRepository) -> None:
        repo.create(KEY, 3, T0)
        assert record(repo, minutes(4)) == [True, True, True, False]
        assert repo.count_in_period(KEY, SEPTEMBER) == 3

    def test_reports_count_after_the_attempt(self, repo: SqliteRepository) -> None:
        repo.create(KEY, 2, T0)
        results = [repo.try_insert_within_limit(KEY, t, SEPTEMBER) for t in minutes(3)]
        assert [(r.inserted, r.used_after, r.monthly_limit) for r in results] == [(True, 1, 2), (True, 2, 2), (False, 2, 2)]

    def test_zero_limit_never_inserts(self, repo: SqliteRepository) -> None:
        repo.create(KEY, 0, T0)
        assert record(repo, minutes(1)) == [False]

    def test_unknown_license_is_not_inserted(self, repo: SqliteRepository) -> None:
        result = repo.try_insert_within_limit(KEY, T1, SEPTEMBER)
        assert (result.inserted, result.used_after, result.monthly_limit) == (False, 0, None)

    def test_suspended_license_is_not_inserted(self, repo: SqliteRepository) -> None:
        repo.create(KEY, 10, T0)
        record(repo, minutes(2))
        repo.set_status(KEY, LicenseStatus.SUSPENDED, T1)
        result = repo.try_insert_within_limit(KEY, "2026-09-11T00:00:00.000Z", SEPTEMBER)
        assert (result.inserted, result.used_after) == (False, 2)

    def test_previous_month_usage_is_not_counted(self, repo: SqliteRepository) -> None:
        repo.create(KEY, 2, T0)
        for t in minutes(2, day="2026-08-20"):
            assert repo.try_insert_within_limit(KEY, t, AUGUST).inserted
        assert record(repo, minutes(3)) == [True, True, False]

    def test_other_licenses_do_not_consume_the_limit(self, repo: SqliteRepository) -> None:
        repo.create(KEY, 1, T0)
        repo.create(OTHER, 5, T0)
        record(repo, minutes(3), key=OTHER)
        assert record(repo, minutes(1)) == [True]

    def test_raised_limit_applies_to_the_next_attempt(self, repo: SqliteRepository) -> None:
        repo.create(KEY, 1, T0)
        assert record(repo, minutes(2)) == [True, False]
        repo.update_limit(KEY, 2, T1)
        assert record(repo, ["2026-09-11T00:00:00.000Z"]) == [True]

    def test_lowered_limit_keeps_existing_rows_and_blocks_new_ones(self, repo: SqliteRepository) -> None:
        repo.create(KEY, 5, T0)
        record(repo, minutes(3))
        repo.update_limit(KEY, 1, T1)
        assert record(repo, ["2026-09-11T00:00:00.000Z"]) == [False]
        assert repo.count_in_period(KEY, SEPTEMBER) == 3


class TestListInRange:
    @pytest.fixture
    def filled(self, repo: SqliteRepository) -> SqliteRepository:
        repo.create(KEY, 100, T0)
        record(repo, minutes(5))
        return repo

    def test_lists_entries_in_id_order(self, filled: SqliteRepository) -> None:
        entries = filled.list_in_range(KEY, SEPTEMBER.start_utc, SEPTEMBER.end_utc, None, 100)
        assert entries == [UsageLogEntry(i + 1, t) for i, t in enumerate(minutes(5))]

    def test_pages_with_after_id_and_limit(self, filled: SqliteRepository) -> None:
        first = filled.list_in_range(KEY, SEPTEMBER.start_utc, SEPTEMBER.end_utc, None, 2)
        second = filled.list_in_range(KEY, SEPTEMBER.start_utc, SEPTEMBER.end_utc, first[-1].id, 2)
        assert [e.id for e in first] == [1, 2]
        assert [e.id for e in second] == [3, 4]

    def test_range_is_half_open(self, filled: SqliteRepository) -> None:
        times = minutes(5)
        entries = filled.list_in_range(KEY, times[1], times[3], None, 100)
        assert [e.used_at for e in entries] == times[1:3]


def test_unavailable_storage_raises_repository_unavailable(repo: SqliteRepository) -> None:
    repo.unavailable = True
    with pytest.raises(RepositoryUnavailable):
        repo.find(KEY)
    with pytest.raises(RepositoryUnavailable):
        repo.try_insert_within_limit(KEY, T1, SEPTEMBER)
