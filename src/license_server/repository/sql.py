"""SQL shared by the D1 repository and the SQLite test double, so tests exercise the production statements.

D1 rejects LIKE/GLOB patterns longer than 50 bytes; keep them out of these statements.
"""

import json
from collections.abc import Mapping
from typing import Any

from license_server.domain.types import (
    AuditAction,
    AuditContext,
    AuditEntry,
    AuditValues,
    License,
    LicenseListItem,
    LicenseSearch,
    LicenseSort,
    LicenseStatus,
    Period,
    SortOrder,
    UsageLogEntry,
)

_LICENSE_COLUMNS = "license_key, monthly_limit, status, created_at, updated_at, memo, public_id"

# A collision hits the unique index and surfaces as "UNIQUE constraint failed", the same retry path as a key collision.
NEW_PUBLIC_ID = "'lic_' || lower(hex(randomblob(8)))"

SELECT_LICENSE = f"SELECT {_LICENSE_COLUMNS} FROM licenses WHERE license_key = ?1"

# ?1 key, ?2 monthly_limit, ?3 now
INSERT_LICENSE = (
    "INSERT INTO licenses (license_key, monthly_limit, status, created_at, updated_at, public_id) "
    f"VALUES (?1, ?2, 'active', ?3, ?3, {NEW_PUBLIC_ID}) RETURNING {_LICENSE_COLUMNS}"
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
    "AND (l.monthly_limit = 0 OR (SELECT COUNT(*) FROM usage_logs AS u "
    "WHERE u.license_key = ?1 AND u.used_at >= ?3 AND u.used_at < ?4) < l.monthly_limit)"
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


_QUALIFIED_LICENSE_COLUMNS = ", ".join(f"l.{column.strip()}" for column in _LICENSE_COLUMNS.split(","))

# ?1 query ('' for none), ?2 status ('' for none).
# instr() instead of LIKE: no 50-byte pattern limit on D1 and no wildcard characters in user input.
# lower() folds ASCII only, which is enough for keys (always lowercase) and English words in memos.
_SEARCH_FILTER = (
    "FROM licenses AS l "
    "WHERE (?1 = '' OR instr(lower(l.memo), lower(?1)) > 0 OR instr(l.license_key, lower(?1)) > 0) "
    "AND (?2 = '' OR l.status = ?2) "
)

COUNT_LICENSES = f"SELECT COUNT(*) AS total {_SEARCH_FILTER}"

# Only these fixed fragments ever reach ORDER BY; request values select among them and are never interpolated.
_SORT_COLUMNS = {LicenseSort.CREATED_AT: "l.created_at", LicenseSort.MONTHLY_LIMIT: "l.monthly_limit", LicenseSort.USED: "used"}
_SORT_DIRECTIONS = {SortOrder.ASC: "ASC", SortOrder.DESC: "DESC"}


def search_licenses(sort: LicenseSort, order: SortOrder) -> str:
    """...?3 period start, ?4 period end, ?5 limit, ?6 offset. Ties fall back to newest first, then the key."""
    tie_break = "" if sort is LicenseSort.CREATED_AT else ", l.created_at DESC"
    return (
        f"SELECT {_QUALIFIED_LICENSE_COLUMNS}, "
        "(SELECT COUNT(*) FROM usage_logs AS u "
        "WHERE u.license_key = l.license_key AND u.used_at >= ?3 AND u.used_at < ?4) AS used "
        f"{_SEARCH_FILTER}"
        f"ORDER BY {_SORT_COLUMNS[sort]} {_SORT_DIRECTIONS[order]}{tie_break}, l.license_key LIMIT ?5 OFFSET ?6"
    )

SELECT_LICENSE_BY_PUBLIC_ID = f"SELECT {_LICENSE_COLUMNS} FROM licenses WHERE public_id = ?1"

SELECT_LICENSE_BY_REQUEST_ID = (
    f"SELECT {_QUALIFIED_LICENSE_COLUMNS} FROM licenses AS l "
    "JOIN console_audit_logs AS a ON a.license_key = l.license_key WHERE a.request_id = ?1"
)

# ?1 key, ?2 limit
LIST_AUDITS = (
    "SELECT id, operator_email, action, before_json, after_json, created_at FROM console_audit_logs "
    "WHERE license_key = ?1 ORDER BY id DESC LIMIT ?2"
)

# Audit statements share ?1 request_id, ?2 operator_email, ?3 source_ip ('' for none), ?4 created_at.
# NULLIF avoids binding None, which the Workers runtime does not pass to D1 as NULL reliably.
# CAST keeps integers integral inside json_object even if the runtime binds numbers as REAL.
_AUDIT_INSERT = (
    "INSERT INTO console_audit_logs "
    "(request_id, operator_email, action, license_key, before_json, after_json, source_ip, created_at) "
)

# ...?5 key, ?6 monthly_limit, ?7 memo. Runs after INSERT_LICENSE_WITH_MEMO in the same batch.
INSERT_ISSUE_AUDIT = (
    f"{_AUDIT_INSERT}VALUES (?1, ?2, 'issue', ?5, NULL, "
    "json_object('monthly_limit', CAST(?6 AS INTEGER), 'memo', ?7), NULLIF(?3, ''), ?4)"
)

# ?1 key, ?2 monthly_limit, ?3 memo, ?4 now
INSERT_LICENSE_WITH_MEMO = (
    "INSERT INTO licenses (license_key, monthly_limit, status, created_at, updated_at, memo, public_id) "
    f"VALUES (?1, ?2, 'active', ?4, ?4, ?3, {NEW_PUBLIC_ID}) RETURNING {_LICENSE_COLUMNS}"
)

# ...?5 key, ?6 new monthly_limit. Reads the before-value from the row, so it must run before the UPDATE.
INSERT_LIMIT_AUDIT = (
    f"{_AUDIT_INSERT}SELECT ?1, ?2, 'update_limit', license_key, json_object('monthly_limit', monthly_limit), "
    "json_object('monthly_limit', CAST(?6 AS INTEGER)), NULLIF(?3, ''), ?4 FROM licenses WHERE license_key = ?5"
)

# ...?5 key, ?6 new status, ?7 action. Only a real change is recorded.
INSERT_STATUS_AUDIT = (
    f"{_AUDIT_INSERT}SELECT ?1, ?2, ?7, license_key, json_object('status', status), "
    "json_object('status', ?6), NULLIF(?3, ''), ?4 FROM licenses WHERE license_key = ?5 AND status <> ?6"
)

# ...?5 key, ?6 new memo
INSERT_MEMO_AUDIT = (
    f"{_AUDIT_INSERT}SELECT ?1, ?2, 'update_memo', license_key, json_object('memo', memo), "
    "json_object('memo', ?6), NULLIF(?3, ''), ?4 FROM licenses WHERE license_key = ?5"
)

INSERT_SIGN_IN_AUDIT = f"{_AUDIT_INSERT}VALUES (?1, ?2, 'sign_in', NULL, NULL, NULL, NULLIF(?3, ''), ?4)"

# ?1 key, ?2 status, ?3 now. Leaves updated_at alone when the status is already the requested one.
UPDATE_STATUS_IF_CHANGED = (
    "UPDATE licenses SET status = ?2, updated_at = ?3 WHERE license_key = ?1 AND status <> ?2 "
    f"RETURNING {_LICENSE_COLUMNS}"
)

# ?1 key, ?2 memo, ?3 now
UPDATE_MEMO = f"UPDATE licenses SET memo = ?2, updated_at = ?3 WHERE license_key = ?1 RETURNING {_LICENSE_COLUMNS}"

Statement = tuple[str, tuple[Any, ...]]

_STATUS_ACTIONS = {LicenseStatus.SUSPENDED: AuditAction.SUSPEND, LicenseStatus.ACTIVE: AuditAction.ACTIVATE}


def _audit_params(ctx: AuditContext) -> tuple[Any, ...]:
    return (ctx.request_id, ctx.operator.email, ctx.source_ip or "", ctx.now_utc)


def filter_params(criteria: LicenseSearch) -> tuple[Any, ...]:
    return (criteria.query or "", criteria.status.value if criteria.status else "")


def search_params(criteria: LicenseSearch, period: Period, limit: int, offset: int) -> tuple[Any, ...]:
    return (*filter_params(criteria), period.start_utc, period.end_utc, limit, offset)


# Each audited write is a batch whose last statement returns the license row (none when nothing changed).
def create_audited(license_key: str, monthly_limit: int, memo: str, ctx: AuditContext) -> list[Statement]:
    return [
        (INSERT_LICENSE_WITH_MEMO, (license_key, monthly_limit, memo, ctx.now_utc)),
        (INSERT_ISSUE_AUDIT, (*_audit_params(ctx), license_key, monthly_limit, memo)),
    ]


def update_limit_audited(license_key: str, monthly_limit: int, ctx: AuditContext) -> list[Statement]:
    return [
        (INSERT_LIMIT_AUDIT, (*_audit_params(ctx), license_key, monthly_limit)),
        (UPDATE_LIMIT, (license_key, monthly_limit, ctx.now_utc)),
    ]


def set_status_audited(license_key: str, status: LicenseStatus, ctx: AuditContext) -> list[Statement]:
    action = _STATUS_ACTIONS[status].value
    return [
        (INSERT_STATUS_AUDIT, (*_audit_params(ctx), license_key, status.value, action)),
        (UPDATE_STATUS_IF_CHANGED, (license_key, status.value, ctx.now_utc)),
    ]


def update_memo_audited(license_key: str, memo: str, ctx: AuditContext) -> list[Statement]:
    return [
        (INSERT_MEMO_AUDIT, (*_audit_params(ctx), license_key, memo)),
        (UPDATE_MEMO, (license_key, memo, ctx.now_utc)),
    ]


def record_sign_in(ctx: AuditContext) -> list[Statement]:
    return [(INSERT_SIGN_IN_AUDIT, _audit_params(ctx))]


def is_duplicate_request(error: Exception) -> bool:
    return "UNIQUE constraint failed: console_audit_logs.request_id" in str(error)


def is_unique_violation(error: Exception) -> bool:
    return "UNIQUE constraint failed" in str(error)


def list_item_from_row(row: Mapping[str, Any]) -> LicenseListItem:
    return LicenseListItem(license=license_from_row(row), used_this_month=int(row["used"]))


def _audit_values(text: Any) -> AuditValues | None:
    if text is None:
        return None
    values: AuditValues = json.loads(str(text))
    return values


def audit_from_row(row: Mapping[str, Any]) -> AuditEntry:
    return AuditEntry(
        id=int(row["id"]),
        operator_email=str(row["operator_email"]),
        action=AuditAction(row["action"]),
        before=_audit_values(row["before_json"]),
        after=_audit_values(row["after_json"]),
        created_at=str(row["created_at"]),
    )


def license_from_row(row: Mapping[str, Any]) -> License:
    return License(
        license_key=str(row["license_key"]),
        monthly_limit=int(row["monthly_limit"]),
        status=LicenseStatus(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        memo=str(row["memo"]),
        public_id=None if row["public_id"] is None else str(row["public_id"]),
    )


def optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def usage_entry_from_row(row: Mapping[str, Any]) -> UsageLogEntry:
    return UsageLogEntry(id=int(row["id"]), used_at=str(row["used_at"]))
