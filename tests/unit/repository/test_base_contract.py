from license_server.domain.errors import ServiceError
from license_server.domain.types import License, LicenseStatus, Period, UsageLogEntry
from license_server.repository.base import (
    InsertResult,
    LicenseRepository,
    RepositoryUnavailable,
    UsageRepository,
)


class StubLicenseRepository:
    def find(self, license_key: str) -> License | None:
        return None

    def create(self, license_key: str, monthly_limit: int, now_utc: str) -> License:
        return License(license_key, monthly_limit, LicenseStatus.ACTIVE, now_utc, now_utc)

    def update_limit(self, license_key: str, monthly_limit: int, now_utc: str) -> License | None:
        return None

    def set_status(self, license_key: str, status: LicenseStatus, now_utc: str) -> License | None:
        return None


class StubUsageRepository:
    def try_insert_within_limit(self, license_key: str, used_at: str, period: Period) -> InsertResult:
        return InsertResult(inserted=False, used_after=0, monthly_limit=None)

    def count_in_period(self, license_key: str, period: Period) -> int:
        return 0

    def list_in_range(
        self, license_key: str, start_utc: str, end_utc: str, after_id: int | None, limit: int
    ) -> list[UsageLogEntry]:
        return []


def test_structural_implementations_satisfy_repository_protocols() -> None:
    assert isinstance(StubLicenseRepository(), LicenseRepository)
    assert isinstance(StubUsageRepository(), UsageRepository)


def test_incomplete_implementation_does_not_satisfy_protocol() -> None:
    class MissingSetStatus:
        def find(self, license_key: str) -> License | None:
            return None

    assert not isinstance(MissingSetStatus(), LicenseRepository)


def test_insert_result_reports_outcome_and_count() -> None:
    result = InsertResult(inserted=True, used_after=5, monthly_limit=10)
    assert (result.inserted, result.used_after, result.monthly_limit) == (True, 5, 10)


def test_repository_unavailable_is_distinct_from_service_errors() -> None:
    error = RepositoryUnavailable("d1 timeout")
    assert isinstance(error, Exception)
    assert not isinstance(error, ServiceError)
