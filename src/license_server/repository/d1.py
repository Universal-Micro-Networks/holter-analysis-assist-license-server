import logging
from collections.abc import Callable, Mapping
from typing import Any

from license_server.domain.types import License, LicenseStatus, Period, UsageLogEntry
from license_server.repository import sql
from license_server.repository.base import DuplicateLicenseKey, InsertResult, RepositoryUnavailable

logger = logging.getLogger(__name__)

RunSync = Callable[[Any], Any]


def _pyodide_run_sync(awaitable: Any) -> Any:
    from pyodide.ffi import run_sync

    return run_sync(awaitable)


class D1Repository:
    """Both repository protocols on a D1 binding.

    The `workers` SDK already converts D1 results to Python (`first()` -> dict, `batch()` -> list[dict]),
    so no `.to_py()` calls are needed (they would raise AttributeError).
    """

    def __init__(self, db: Any, run_sync: RunSync = _pyodide_run_sync) -> None:
        self._db = db
        self._run_sync = run_sync

    def find(self, license_key: str) -> License | None:
        row = self._first(sql.SELECT_LICENSE, license_key)
        return sql.license_from_row(row) if row else None

    def create(self, license_key: str, monthly_limit: int, now_utc: str) -> License:
        try:
            row = self._run_sync(self._db.prepare(sql.INSERT_LICENSE).bind(license_key, monthly_limit, now_utc).first())
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise DuplicateLicenseKey(license_key) from error
            raise self._unavailable(error) from error
        return sql.license_from_row(row)

    def update_limit(self, license_key: str, monthly_limit: int, now_utc: str) -> License | None:
        row = self._first(sql.UPDATE_LIMIT, license_key, monthly_limit, now_utc)
        return sql.license_from_row(row) if row else None

    def set_status(self, license_key: str, status: LicenseStatus, now_utc: str) -> License | None:
        row = self._first(sql.UPDATE_STATUS, license_key, status.value, now_utc)
        return sql.license_from_row(row) if row else None

    def try_insert_within_limit(self, license_key: str, used_at: str, period: Period) -> InsertResult:
        insert = self._db.prepare(sql.INSERT_USAGE_WITHIN_LIMIT).bind(
            license_key, used_at, period.start_utc, period.end_utc
        )
        count = self._db.prepare(sql.COUNT_AND_LIMIT).bind(license_key, period.start_utc, period.end_utc)
        insert_result, count_result = self._call(lambda: self._db.batch([insert, count]))
        row = count_result["results"][0]
        return InsertResult(
            inserted=int(insert_result["meta"]["changes"]) == 1,
            used_after=int(row["used"]),
            monthly_limit=sql.optional_int(row["monthly_limit"]),
        )

    def count_in_period(self, license_key: str, period: Period) -> int:
        row = self._first(sql.COUNT_IN_PERIOD, license_key, period.start_utc, period.end_utc)
        return int(row["used"]) if row else 0

    def list_in_range(
        self, license_key: str, start_utc: str, end_utc: str, after_id: int | None, limit: int
    ) -> list[UsageLogEntry]:
        result = self._call(
            lambda: self._db.prepare(sql.LIST_IN_RANGE).bind(license_key, start_utc, end_utc, after_id or 0, limit).all()
        )
        return [sql.usage_entry_from_row(row) for row in result["results"]]

    def _first(self, query: str, *params: Any) -> Mapping[str, Any] | None:
        row: Mapping[str, Any] | None = self._call(lambda: self._db.prepare(query).bind(*params).first())
        return row

    def _call(self, make_awaitable: Callable[[], Any]) -> Any:
        try:
            return self._run_sync(make_awaitable())
        except Exception as error:
            raise self._unavailable(error) from error

    @staticmethod
    def _unavailable(error: Exception) -> RepositoryUnavailable:
        # Bound parameters (license keys) are not part of D1 error messages, so the text is safe to log.
        logger.error("D1 call failed: %s: %s", type(error).__name__, error)
        return RepositoryUnavailable(f"{type(error).__name__}: {error}")
