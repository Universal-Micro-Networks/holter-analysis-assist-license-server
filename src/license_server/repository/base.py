from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from license_server.domain.types import License, LicenseStatus, Period, UsageLogEntry


class RepositoryUnavailable(Exception):
    """Storage failure. The message is for logs only and must never reach API responses."""


class DuplicateLicenseKey(Exception):
    """Raised by `LicenseRepository.create` when the key already exists (callers regenerate and retry)."""


@dataclass(frozen=True)
class InsertResult:
    inserted: bool
    used_after: int
    monthly_limit: int | None  # None when the license does not exist


@runtime_checkable
class LicenseRepository(Protocol):
    def find(self, license_key: str) -> License | None: ...

    def create(self, license_key: str, monthly_limit: int, now_utc: str) -> License: ...

    def update_limit(self, license_key: str, monthly_limit: int, now_utc: str) -> License | None: ...

    def set_status(self, license_key: str, status: LicenseStatus, now_utc: str) -> License | None: ...


@runtime_checkable
class UsageRepository(Protocol):
    def try_insert_within_limit(self, license_key: str, used_at: str, period: Period) -> InsertResult:
        """Insert one row only if the license is active and its count in `period` is below monthly_limit.

        The check and the insert must be a single atomic statement so concurrent calls cannot exceed the limit.
        """
        ...

    def count_in_period(self, license_key: str, period: Period) -> int: ...

    def list_in_range(
        self, license_key: str, start_utc: str, end_utc: str, after_id: int | None, limit: int
    ) -> list[UsageLogEntry]: ...
