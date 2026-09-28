import sqlite3
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from license_server.domain.types import License, LicenseStatus, Period, UsageLogEntry
from license_server.repository import sql
from license_server.repository.base import DuplicateLicenseKey, InsertResult, RepositoryUnavailable

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
