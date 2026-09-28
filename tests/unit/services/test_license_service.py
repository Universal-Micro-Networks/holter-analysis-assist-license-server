from collections.abc import Iterator

import pytest

from license_server.domain.errors import ErrorCode, ServiceError
from license_server.domain.license_key import is_valid_license_key
from license_server.domain.types import LicenseStatus
from license_server.repository.base import RepositoryUnavailable
from license_server.services.license_service import LicenseService
from tests.conftest import FixedClock
from tests.fakes.sqlite_repository import SqliteRepository

UNKNOWN = "lk_" + "f" * 32


@pytest.fixture
def service(repo: SqliteRepository, clock: FixedClock) -> LicenseService:
    return LicenseService(repo, clock)


def error_code(call) -> ErrorCode:
    with pytest.raises(ServiceError) as caught:
        call()
    return caught.value.code


class TestIssue:
    def test_issues_active_license_with_generated_key(self, service: LicenseService, repo: SqliteRepository) -> None:
        license_ = service.issue(100)
        assert is_valid_license_key(license_.license_key)
        assert (license_.monthly_limit, license_.status) == (100, LicenseStatus.ACTIVE)
        assert license_.created_at == "2026-09-15T03:00:00.000Z"
        assert repo.find(license_.license_key) == license_

    def test_retries_when_generated_key_collides(self, repo: SqliteRepository, clock: FixedClock) -> None:
        existing = LicenseService(repo, clock).issue(1).license_key
        fresh = "lk_" + "1" * 32
        keys: Iterator[str] = iter([existing, existing, fresh])
        issued = LicenseService(repo, clock, key_generator=lambda: next(keys)).issue(5)
        assert issued.license_key == fresh

    def test_gives_up_after_three_collisions(self, repo: SqliteRepository, clock: FixedClock) -> None:
        existing = LicenseService(repo, clock).issue(1).license_key
        service = LicenseService(repo, clock, key_generator=lambda: existing)
        with pytest.raises(RepositoryUnavailable):
            service.issue(5)

    @pytest.mark.parametrize("limit", [0, 1, 1_000_000])
    def test_accepts_limits_in_range(self, service: LicenseService, limit: int) -> None:
        assert service.issue(limit).monthly_limit == limit

    @pytest.mark.parametrize("limit", [-1, 1_000_001, 1.5, 10.0, True, False, "10", None])
    def test_rejects_invalid_limits(self, service: LicenseService, limit: object) -> None:
        assert error_code(lambda: service.issue(limit)) is ErrorCode.INVALID_REQUEST  # type: ignore[arg-type]


class TestRequireActive:
    def test_returns_active_license(self, service: LicenseService) -> None:
        issued = service.issue(10)
        assert service.require_active(issued.license_key) == issued

    def test_unknown_key_is_license_invalid(self, service: LicenseService) -> None:
        assert error_code(lambda: service.require_active(UNKNOWN)) is ErrorCode.LICENSE_INVALID

    def test_suspended_license_is_license_suspended(self, service: LicenseService) -> None:
        key = service.issue(10).license_key
        service.suspend(key)
        assert error_code(lambda: service.require_active(key)) is ErrorCode.LICENSE_SUSPENDED


class TestAdministration:
    def test_get_returns_license_or_not_found(self, service: LicenseService) -> None:
        issued = service.issue(10)
        assert service.get(issued.license_key) == issued
        assert error_code(lambda: service.get(UNKNOWN)) is ErrorCode.LICENSE_NOT_FOUND

    def test_update_limit_changes_limit_and_timestamp(self, service: LicenseService, clock: FixedClock) -> None:
        key = service.issue(10).license_key
        clock.advance(hours=1)
        updated = service.update_limit(key, 20)
        assert (updated.monthly_limit, updated.updated_at) == (20, "2026-09-15T04:00:00.000Z")

    def test_update_limit_validates_value(self, service: LicenseService) -> None:
        key = service.issue(10).license_key
        assert error_code(lambda: service.update_limit(key, -5)) is ErrorCode.INVALID_REQUEST

    @pytest.mark.parametrize("operation", ["update_limit", "suspend", "activate"])
    def test_operations_on_unknown_key_are_not_found(self, service: LicenseService, operation: str) -> None:
        args = (UNKNOWN, 5) if operation == "update_limit" else (UNKNOWN,)
        assert error_code(lambda: getattr(service, operation)(*args)) is ErrorCode.LICENSE_NOT_FOUND

    def test_suspend_and_activate_are_idempotent(self, service: LicenseService) -> None:
        key = service.issue(10).license_key
        assert service.suspend(key).status is LicenseStatus.SUSPENDED
        assert service.suspend(key).status is LicenseStatus.SUSPENDED
        assert service.activate(key).status is LicenseStatus.ACTIVE
        assert service.activate(key).status is LicenseStatus.ACTIVE

    def test_suspending_keeps_usage_history(self, service: LicenseService, repo: SqliteRepository) -> None:
        from license_server.domain.period import month_period_of

        key = service.issue(10).license_key
        september = month_period_of(2026, 9)
        repo.try_insert_within_limit(key, "2026-09-10T00:00:00.000Z", september)
        service.suspend(key)
        assert repo.count_in_period(key, september) == 1
