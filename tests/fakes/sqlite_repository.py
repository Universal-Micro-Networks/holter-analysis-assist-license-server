import sqlite3
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

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
from license_server.repository import sql
from license_server.repository.base import (
    DuplicateLicenseKey,
    DuplicateSubmission,
    InsertResult,
    RepositoryUnavailable,
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

F = TypeVar("F", bound=Callable[..., Any])


def _guard(method: F) -> F:
    @wraps(method)
    def wrapper(self: "SqliteRepository", *args: Any, **kwargs: Any) -> Any:
        if self.unavailable:
            raise RepositoryUnavailable("simulated storage outage")
        return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


class SqliteRepository:
    """In-memory test double for both repository protocols, using the production migrations and SQL."""

    def __init__(self) -> None:
        self.unavailable = False
        self._db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        # D1 enforces foreign keys by default; plain SQLite does not.
        self._db.execute("PRAGMA foreign_keys = ON")
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            self._db.executescript(migration.read_text(encoding="utf-8"))

    @_guard
    def find(self, license_key: str) -> License | None:
        row = self._db.execute(sql.SELECT_LICENSE, (license_key,)).fetchone()
        return sql.license_from_row(row) if row else None

    @_guard
    def create(self, license_key: str, monthly_limit: int, now_utc: str) -> License:
        try:
            row = self._db.execute(sql.INSERT_LICENSE, (license_key, monthly_limit, now_utc)).fetchone()
        except sqlite3.IntegrityError as error:
            if "UNIQUE" in str(error):
                raise DuplicateLicenseKey(license_key) from error
            raise
        return sql.license_from_row(row)

    @_guard
    def update_limit(self, license_key: str, monthly_limit: int, now_utc: str) -> License | None:
        row = self._db.execute(sql.UPDATE_LIMIT, (license_key, monthly_limit, now_utc)).fetchone()
        return sql.license_from_row(row) if row else None

    @_guard
    def set_status(self, license_key: str, status: LicenseStatus, now_utc: str) -> License | None:
        row = self._db.execute(sql.UPDATE_STATUS, (license_key, status.value, now_utc)).fetchone()
        return sql.license_from_row(row) if row else None

    @_guard
    def try_insert_within_limit(self, license_key: str, used_at: str, period: Period) -> InsertResult:
        self._db.execute("BEGIN")
        try:
            inserted = self._db.execute(
                sql.INSERT_USAGE_WITHIN_LIMIT, (license_key, used_at, period.start_utc, period.end_utc)
            ).rowcount
            row = self._db.execute(sql.COUNT_AND_LIMIT, (license_key, period.start_utc, period.end_utc)).fetchone()
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        return InsertResult(
            inserted=inserted == 1, used_after=int(row["used"]), monthly_limit=sql.optional_int(row["monthly_limit"])
        )

    @_guard
    def count_in_period(self, license_key: str, period: Period) -> int:
        row = self._db.execute(sql.COUNT_IN_PERIOD, (license_key, period.start_utc, period.end_utc)).fetchone()
        return int(row["used"])

    @_guard
    def list_in_range(
        self, license_key: str, start_utc: str, end_utc: str, after_id: int | None, limit: int
    ) -> list[UsageLogEntry]:
        rows = self._db.execute(sql.LIST_IN_RANGE, (license_key, start_utc, end_utc, after_id or 0, limit)).fetchall()
        return [sql.usage_entry_from_row(row) for row in rows]

    @_guard
    def search(self, criteria: LicenseSearch, period: Period, limit: int, offset: int) -> list[LicenseListItem]:
        statement = sql.search_licenses(criteria.sort, criteria.order)
        rows = self._db.execute(statement, sql.search_params(criteria, period, limit, offset)).fetchall()
        return [sql.list_item_from_row(row) for row in rows]

    @_guard
    def count_matching(self, criteria: LicenseSearch) -> int:
        return int(self._db.execute(sql.COUNT_LICENSES, sql.filter_params(criteria)).fetchone()["total"])

    @_guard
    def find_by_public_id(self, public_id: str) -> License | None:
        row = self._db.execute(sql.SELECT_LICENSE_BY_PUBLIC_ID, (public_id,)).fetchone()
        return sql.license_from_row(row) if row else None

    @_guard
    def find_license_by_request_id(self, request_id: str) -> License | None:
        row = self._db.execute(sql.SELECT_LICENSE_BY_REQUEST_ID, (request_id,)).fetchone()
        return sql.license_from_row(row) if row else None

    @_guard
    def create_audited(self, license_key: str, monthly_limit: int, memo: str, ctx: AuditContext) -> License:
        row = self._batch(sql.create_audited(license_key, monthly_limit, memo, ctx), ctx, first_row=True)
        return sql.license_from_row(row)  # type: ignore[arg-type]

    @_guard
    def update_limit_audited(self, license_key: str, monthly_limit: int, ctx: AuditContext) -> License | None:
        return self._license_or_none(self._batch(sql.update_limit_audited(license_key, monthly_limit, ctx), ctx))

    @_guard
    def set_status_audited(self, license_key: str, status: LicenseStatus, ctx: AuditContext) -> License | None:
        return self._license_or_none(self._batch(sql.set_status_audited(license_key, status, ctx), ctx))

    @_guard
    def update_memo_audited(self, license_key: str, memo: str, ctx: AuditContext) -> License | None:
        return self._license_or_none(self._batch(sql.update_memo_audited(license_key, memo, ctx), ctx))

    @_guard
    def record_sign_in(self, ctx: AuditContext) -> None:
        self._batch(sql.record_sign_in(ctx), ctx)

    @_guard
    def list_audits(self, license_key: str, limit: int) -> list[AuditEntry]:
        rows = self._db.execute(sql.LIST_AUDITS, (license_key, limit)).fetchall()
        return [sql.audit_from_row(row) for row in rows]

    def count_audits(self, action: str) -> int:
        row = self._db.execute("SELECT COUNT(*) AS n FROM console_audit_logs WHERE action = ?", (action,)).fetchone()
        return int(row["n"])

    def count_licenses(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) AS n FROM licenses").fetchone()["n"])

    def audit_source_ips(self, license_key: str) -> list[str | None]:
        rows = self._db.execute(
            "SELECT source_ip FROM console_audit_logs WHERE license_key = ? ORDER BY id", (license_key,)
        ).fetchall()
        return [row["source_ip"] for row in rows]

    def _batch(self, statements: list[sql.Statement], ctx: AuditContext, first_row: bool = False) -> Any:
        """Run statements in one transaction like D1 `batch()`; return the first row of the first or last statement."""
        self._db.execute("BEGIN")
        try:
            rows = [self._db.execute(query, params).fetchone() for query, params in statements]
            self._db.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self._db.execute("ROLLBACK")
            if sql.is_duplicate_request(error):
                raise DuplicateSubmission(ctx.request_id) from error
            if sql.is_unique_violation(error):
                raise DuplicateLicenseKey("duplicate license key or public id") from error
            raise
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        return rows[0] if first_row else rows[-1]

    @staticmethod
    def _license_or_none(row: Any) -> License | None:
        return sql.license_from_row(row) if row else None
