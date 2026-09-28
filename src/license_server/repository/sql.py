"""SQL shared by the D1 repository and the SQLite test double, so tests exercise the production statements.

D1 rejects LIKE/GLOB patterns longer than 50 bytes; keep them out of these statements.
"""

from collections.abc import Mapping
from typing import Any

from license_server.domain.types import License, LicenseStatus, UsageLogEntry

_LICENSE_COLUMNS = "license_key, monthly_limit, status, created_at, updated_at"

SELECT_LICENSE = f"SELECT {_LICENSE_COLUMNS} FROM licenses WHERE license_key = ?1"

# ?1 key, ?2 monthly_limit, ?3 now
INSERT_LICENSE = (
    "INSERT INTO licenses (license_key, monthly_limit, status, created_at, updated_at) "
    f"VALUES (?1, ?2, 'active', ?3, ?3) RETURNING {_LICENSE_COLUMNS}"
)

# ?1 key, ?2 monthly_limit, ?3 now
UPDATE_LIMIT = (
    f"UPDATE licenses SET monthly_limit = ?2, updated_at = ?3 WHERE license_key = ?1 RETURNING {_LICENSE_COLUMNS}"
)

# ?1 key, ?2 status, ?3 now
UPDATE_STATUS = f"UPDATE licenses SET status = ?2, updated_at = ?3 WHERE license_key = ?1 RETURNING {_LICENSE_COLUMNS}"

# ?1 key, ?2 used_at, ?3 period start, ?4 period end.
# The limit check and the insert must stay one statement: D1 runs statements serially, so this cannot overshoot.
INSERT_USAGE_WITHIN_LIMIT = (
    "INSERT INTO usage_logs (license_key, used_at) "
    "SELECT l.license_key, ?2 FROM licenses AS l "
    "WHERE l.license_key = ?1 AND l.status = 'active' "
    "AND (SELECT COUNT(*) FROM usage_logs AS u "
    "WHERE u.license_key = ?1 AND u.used_at >= ?3 AND u.used_at < ?4) < l.monthly_limit"
)

# ?1 key, ?2 start, ?3 end
COUNT_IN_PERIOD = "SELECT COUNT(*) AS used FROM usage_logs WHERE license_key = ?1 AND used_at >= ?2 AND used_at < ?3"

# ?1 key, ?2 start, ?3 end. Runs in the same batch as INSERT_USAGE_WITHIN_LIMIT so a success needs one round trip.
COUNT_AND_LIMIT = (
    "SELECT (SELECT COUNT(*) FROM usage_logs WHERE license_key = ?1 AND used_at >= ?2 AND used_at < ?3) AS used, "
    "(SELECT monthly_limit FROM licenses WHERE license_key = ?1) AS monthly_limit"
)

# ?1 key, ?2 start, ?3 end, ?4 after id (0 for first page), ?5 limit
LIST_IN_RANGE = (
    "SELECT id, used_at FROM usage_logs "
    "WHERE license_key = ?1 AND used_at >= ?2 AND used_at < ?3 AND id > ?4 "
    "ORDER BY id LIMIT ?5"
)


def license_from_row(row: Mapping[str, Any]) -> License:
    return License(
        license_key=str(row["license_key"]),
        monthly_limit=int(row["monthly_limit"]),
        status=LicenseStatus(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def usage_entry_from_row(row: Mapping[str, Any]) -> UsageLogEntry:
    return UsageLogEntry(id=int(row["id"]), used_at=str(row["used_at"]))
