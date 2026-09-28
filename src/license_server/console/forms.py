"""Parsing of console form and query values into typed values, with per-field Japanese error messages."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

from license_server.console import messages
from license_server.domain.errors import ServiceError
from license_server.domain.period import jst_date_range, jst_month_bounds
from license_server.domain.types import UNLIMITED, LicenseSort, LicenseStatus, Period, SortOrder
from license_server.services.console_service import MAX_QUERY_LENGTH, MemoProblem, memo_problem
from license_server.services.license_service import validate_monthly_limit

_DIGITS = re.compile(r"[0-9]{1,7}")
_MONTH = re.compile(r"([0-9]{4})-([0-9]{2})")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MIN_YEAR = 2000
_MAX_PAGE = 10_000
_MEMO_MESSAGES = {MemoProblem.TOO_LONG: messages.MEMO_TOO_LONG, MemoProblem.CONTROL_CHARACTER: messages.MEMO_CONTROL_CHARACTER}

Form = Mapping[str, str]


class FormInvalid(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("invalid form input")
        self.errors = errors


@dataclass(frozen=True)
class IssueForm:
    monthly_limit: int
    memo: str


def parse_limit(form: Form) -> int:
    raw = form.get("monthly_limit", "").strip()
    if not raw:
        raise FormInvalid({"monthly_limit": messages.LIMIT_REQUIRED})
    if not _DIGITS.fullmatch(raw):
        raise FormInvalid({"monthly_limit": messages.LIMIT_INVALID})
    try:
        return validate_monthly_limit(int(raw))
    except ServiceError as error:
        raise FormInvalid({"monthly_limit": messages.LIMIT_INVALID}) from error


def parse_memo(form: Form) -> str:
    memo = form.get("memo", "").strip()
    problem = memo_problem(memo)
    if problem is not None:
        raise FormInvalid({"memo": _MEMO_MESSAGES[problem]})
    return memo


def parse_issue(form: Form) -> IssueForm:
    errors: dict[str, str] = {}
    limit = _collect(errors, lambda: parse_limit(form)) if "monthly_limit" in form else UNLIMITED
    memo = _collect(errors, lambda: parse_memo(form))
    if errors:
        raise FormInvalid(errors)
    assert isinstance(limit, int) and isinstance(memo, str)
    return IssueForm(monthly_limit=limit, memo=memo)


def parse_search(form: Form) -> tuple[str | None, LicenseStatus | None]:
    query = form.get("q", "").strip()
    status = form.get("status", "").strip()
    errors: dict[str, str] = {}
    if len(query) > MAX_QUERY_LENGTH:
        errors["q"] = messages.QUERY_TOO_LONG
    if status and status not in {member.value for member in LicenseStatus}:
        errors["status"] = messages.STATUS_INVALID
    if errors:
        raise FormInvalid(errors)
    return (query or None), (LicenseStatus(status) if status else None)


def parse_month(value: str | None, now_utc: datetime) -> tuple[int, int]:
    if not value:
        first, _ = jst_month_bounds(now_utc)
        return first.year, first.month
    match = _MONTH.fullmatch(value)
    if match is None or int(match.group(1)) < _MIN_YEAR or not 1 <= int(match.group(2)) <= 12:
        raise FormInvalid({"month": messages.MONTH_INVALID})
    return int(match.group(1)), int(match.group(2))


def parse_date_range(start: str | None, end: str | None, now_utc: datetime) -> tuple[date, date, Period]:
    """Inclusive JST dates (either side defaults to the current month's bound) and the half-open UTC period."""
    month_first, month_last = jst_month_bounds(now_utc)
    errors: dict[str, str] = {}
    first = _parse_date(start, month_first, "from", messages.FROM_INVALID, errors)
    last = _parse_date(end, month_last, "to", messages.TO_INVALID, errors)
    if errors:
        raise FormInvalid(errors)
    assert first is not None and last is not None
    if first > last:
        raise FormInvalid({"from": messages.RANGE_REVERSED})
    return first, last, jst_date_range(first, last)


def parse_page(value: str | None) -> int:
    if value and _DIGITS.fullmatch(value) and 1 <= int(value) <= _MAX_PAGE:
        return int(value)
    return 1


def parse_sort(sort: str | None, order: str | None) -> tuple[LicenseSort, SortOrder]:
    """Unknown values fall back to the defaults (newest first) instead of failing; the list is still usable."""
    known_sort = LicenseSort(sort) if sort in set(LicenseSort) else LicenseSort.CREATED_AT
    known_order = SortOrder(order) if order in set(SortOrder) else SortOrder.DESC
    return known_sort, known_order


def parse_after_id(value: str | None) -> int | None:
    if value and value.isascii() and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _parse_date(value: str | None, default: date, field: str, message: str, errors: dict[str, str]) -> date | None:
    if not value:
        return default
    if _DATE.fullmatch(value):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.year >= _MIN_YEAR:
            return parsed
    errors[field] = message
    return None


def _collect(errors: dict[str, str], parse: Callable[[], object]) -> object:
    try:
        return parse()
    except FormInvalid as invalid:
        errors.update(invalid.errors)
        return None
