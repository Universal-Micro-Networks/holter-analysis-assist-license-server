from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from license_server.domain.types import (
    AuditContext,
    AuditEntry,
    License,
    LicenseListItem,
    LicenseSearch,
    LicenseStatus,
    Period,
    UsageLogEntry,
)


class RepositoryUnavailable(Exception):
    """Storage failure. The message is for logs only and must never reach API responses."""


class DuplicateLicenseKey(Exception):
    """Raised by `LicenseRepository.create` when the key already exists (callers regenerate and retry)."""


class DuplicateSubmission(Exception):
    """The form's request_id was already used; nothing was changed."""

    def __init__(self, request_id: str) -> None:
        super().__init__("request_id already used")
        self.request_id = request_id


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


@runtime_checkable
class ConsoleRepository(Protocol):
    """Console reads and audited writes. Each write stores its audit record atomically with the change.

    Audited writes raise `DuplicateSubmission` (and change nothing) when `ctx.request_id` was already used.
    Audit records have no update or delete operations.
    """

    def search(self, criteria: LicenseSearch, period: Period, limit: int, offset: int) -> list[LicenseListItem]:
        """Ordered by `criteria.sort` / `criteria.order`; `criteria.page` is ignored (use limit and offset)."""
        ...

    def count_matching(self, criteria: LicenseSearch) -> int: ...

    def find_by_public_id(self, public_id: str) -> License | None: ...

    def find_license_by_request_id(self, request_id: str) -> License | None: ...

    def create_audited(self, license_key: str, monthly_limit: int, memo: str, ctx: AuditContext) -> License: ...

    def update_limit_audited(self, license_key: str, monthly_limit: int, ctx: AuditContext) -> License | None: ...

    def set_status_audited(self, license_key: str, status: LicenseStatus, ctx: AuditContext) -> License | None:
        """Returns None when the license is missing or already in `status` (then nothing is recorded)."""
        ...

    def update_memo_audited(self, license_key: str, memo: str, ctx: AuditContext) -> License | None: ...

    def record_sign_in(self, ctx: AuditContext) -> None: ...

    def list_audits(self, license_key: str, limit: int) -> list[AuditEntry]: ...
