from typing import Any

from flask import Blueprint, Response, request

from license_server.domain.errors import ErrorCode, ServiceError
from license_server.domain.license_key import is_valid_license_key
from license_server.domain.period import parse_month, parse_timestamp
from license_server.http.access_log import remember_license_key
from license_server.http.dependencies import guard_admin
from license_server.http.dto import license_dto, usage_entry_dto, usage_summary_dto
from license_server.http.responses import success_response
from license_server.services.usage_service import DEFAULT_PAGE_SIZE

admin_api = Blueprint("admin_api", __name__, url_prefix="/v1/admin")


def _invalid(message: str) -> ServiceError:
    return ServiceError(ErrorCode.INVALID_REQUEST, message)


def _body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise _invalid("The request body must be a JSON object.")
    return body


def _license_key(body: dict[str, Any]) -> str:
    key = body.get("license_key")
    if not is_valid_license_key(key):
        raise _invalid("license_key is missing or malformed.")
    remember_license_key(str(key))
    return str(key)


def _timestamp(body: dict[str, Any], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str):
        raise _invalid(f"{field} must be an ISO 8601 timestamp with a UTC offset.")
    try:
        return parse_timestamp(value)
    except ValueError:
        raise _invalid(f"{field} must be an ISO 8601 timestamp with a UTC offset.") from None


def _after_id(body: dict[str, Any]) -> int | None:
    value = body.get("after_id")
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise _invalid("after_id must be a non-negative integer.")
    return value


@admin_api.post("/licenses")
def issue_license() -> Response:
    deps = guard_admin()
    return success_response(license_dto(deps.licenses.issue(_body().get("monthly_limit"))), status=201)


@admin_api.post("/licenses/get")
def get_license() -> Response:
    deps = guard_admin()
    return success_response(license_dto(deps.licenses.get(_license_key(_body()))))


@admin_api.post("/licenses/update-limit")
def update_limit() -> Response:
    deps = guard_admin()
    body = _body()
    return success_response(license_dto(deps.licenses.update_limit(_license_key(body), body.get("monthly_limit"))))


@admin_api.post("/licenses/suspend")
def suspend_license() -> Response:
    deps = guard_admin()
    return success_response(license_dto(deps.licenses.suspend(_license_key(_body()))))


@admin_api.post("/licenses/activate")
def activate_license() -> Response:
    deps = guard_admin()
    return success_response(license_dto(deps.licenses.activate(_license_key(_body()))))


@admin_api.post("/usage/logs")
def usage_logs() -> Response:
    deps = guard_admin()
    body = _body()
    entries, next_after_id = deps.usage.list_logs(
        _license_key(body),
        _timestamp(body, "from"),
        _timestamp(body, "to"),
        _after_id(body),
        body.get("limit", DEFAULT_PAGE_SIZE),
    )
    return success_response({"entries": [usage_entry_dto(e) for e in entries], "next_after_id": next_after_id})


@admin_api.post("/usage/monthly")
def monthly_usage() -> Response:
    deps = guard_admin()
    body = _body()
    key = _license_key(body)
    month = body.get("month")
    try:
        year, month_number = parse_month(month if isinstance(month, str) else "")
    except ValueError:
        raise _invalid("month must be in YYYY-MM format.") from None
    return success_response(usage_summary_dto(deps.usage.monthly_count(key, year, month_number)))
