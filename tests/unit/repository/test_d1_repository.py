from typing import Any

import pytest

from license_server.domain.period import month_period_of
from license_server.domain.types import AuditContext, LicenseStatus, Operator, UsageLogEntry
from license_server.repository.base import (
    ConsoleRepository,
    DuplicateLicenseKey,
    DuplicateSubmission,
    LicenseRepository,
    RepositoryUnavailable,
    UsageRepository,
)
from license_server.repository.d1 import D1Repository
from tests.fakes.fake_d1 import FakeD1Binding

KEY = "lk_" + "a" * 32
T0 = "2026-09-01T00:00:00.000Z"
T1 = "2026-09-02T00:00:00.000Z"
SEPTEMBER = month_period_of(2026, 9)


def identity(value: Any) -> Any:
    return value


@pytest.fixture
def binding() -> FakeD1Binding:
    return FakeD1Binding()


@pytest.fixture
def repo(binding: FakeD1Binding) -> D1Repository:
    return D1Repository(binding, run_sync=identity)


def test_satisfies_both_repository_protocols(repo: D1Repository) -> None:
    assert isinstance(repo, LicenseRepository)
    assert isinstance(repo, UsageRepository)


def test_create_find_update_and_suspend(repo: D1Repository) -> None:
    assert repo.find(KEY) is None
    created = repo.create(KEY, 2, T0)
    assert repo.find(KEY) == created
    updated = repo.update_limit(KEY, 5, T1)
    assert updated is not None and (updated.monthly_limit, updated.updated_at) == (5, T1)
    suspended = repo.set_status(KEY, LicenseStatus.SUSPENDED, T1)
    assert suspended is not None and suspended.status is LicenseStatus.SUSPENDED


def test_update_and_set_status_of_unknown_key_return_none(repo: D1Repository) -> None:
    assert repo.update_limit(KEY, 5, T1) is None
    assert repo.set_status(KEY, LicenseStatus.ACTIVE, T1) is None


def test_duplicate_key_maps_to_duplicate_license_key(repo: D1Repository) -> None:
    repo.create(KEY, 2, T0)
    with pytest.raises(DuplicateLicenseKey):
        repo.create(KEY, 2, T0)


def test_insert_within_limit_uses_one_batch_and_reports_count(repo: D1Repository, binding: FakeD1Binding) -> None:
    repo.create(KEY, 1, T0)
    binding.calls.clear()
    first = repo.try_insert_within_limit(KEY, "2026-09-10T00:00:00.000Z", SEPTEMBER)
    second = repo.try_insert_within_limit(KEY, "2026-09-10T00:01:00.000Z", SEPTEMBER)
    assert (first.inserted, first.used_after, first.monthly_limit) == (True, 1, 1)
    assert (second.inserted, second.used_after, second.monthly_limit) == (False, 1, 1)
    assert binding.calls == ["batch", "batch"]


def test_count_and_list(repo: D1Repository) -> None:
    repo.create(KEY, 10, T0)
    times = [f"2026-09-10T00:0{m}:00.000Z" for m in range(3)]
    for t in times:
        repo.try_insert_within_limit(KEY, t, SEPTEMBER)
    assert repo.count_in_period(KEY, SEPTEMBER) == 3
    page = repo.list_in_range(KEY, SEPTEMBER.start_utc, SEPTEMBER.end_utc, 1, 10)
    assert page == [UsageLogEntry(2, times[1]), UsageLogEntry(3, times[2])]


@pytest.mark.parametrize(
    "call",
    [
        lambda r: r.find(KEY),
        lambda r: r.create(KEY, 1, T0),
        lambda r: r.update_limit(KEY, 1, T0),
        lambda r: r.set_status(KEY, LicenseStatus.ACTIVE, T0),
        lambda r: r.try_insert_within_limit(KEY, T1, SEPTEMBER),
        lambda r: r.count_in_period(KEY, SEPTEMBER),
        lambda r: r.list_in_range(KEY, T0, T1, None, 10),
    ],
)
def test_d1_failures_become_repository_unavailable(repo: D1Repository, binding: FakeD1Binding, call) -> None:
    binding.fail_with = "D1_ERROR: Network connection lost."
    with pytest.raises(RepositoryUnavailable):
        call(repo)


class TestConsoleRepository:
    def test_satisfies_console_protocol(self, repo: D1Repository) -> None:
        assert isinstance(repo, ConsoleRepository)

    def test_each_audited_write_is_a_single_batch(self, repo: D1Repository, binding: FakeD1Binding) -> None:
        repo.create_audited(KEY, 5, "", ctx("r1"))
        repo.update_limit_audited(KEY, 6, ctx("r2"))
        repo.set_status_audited(KEY, LicenseStatus.SUSPENDED, ctx("r3"))
        repo.update_memo_audited(KEY, "m", ctx("r4"))
        repo.record_sign_in(ctx("r5"))
        assert binding.calls == ["batch"] * 5

    def test_reused_request_id_maps_to_duplicate_submission(self, repo: D1Repository) -> None:
        repo.create_audited(KEY, 5, "", ctx("r1"))
        with pytest.raises(DuplicateSubmission) as raised:
            repo.update_limit_audited(KEY, 6, ctx("r1"))
        assert raised.value.request_id == "r1"

    def test_duplicate_key_on_audited_issue_maps_to_duplicate_license_key(self, repo: D1Repository) -> None:
        repo.create_audited(KEY, 5, "", ctx("r1"))
        with pytest.raises(DuplicateLicenseKey):
            repo.create_audited(KEY, 5, "", ctx("r2"))

    @pytest.mark.parametrize(
        "call",
        [
            lambda r: r.create_audited(KEY, 1, "", ctx("r1")),
            lambda r: r.update_limit_audited(KEY, 1, ctx("r1")),
            lambda r: r.record_sign_in(ctx("r1")),
            lambda r: r.find_by_public_id("lic_0000000000000000"),
            lambda r: r.list_audits(KEY, 10),
        ],
    )
    def test_other_failures_become_repository_unavailable(self, repo: D1Repository, binding: FakeD1Binding, call) -> None:
        binding.fail_with = "D1_ERROR: Network connection lost."
        with pytest.raises(RepositoryUnavailable):
            call(repo)


def ctx(request_id: str) -> AuditContext:
    return AuditContext(Operator("ops@example.com", "sub"), request_id, None, T0)
