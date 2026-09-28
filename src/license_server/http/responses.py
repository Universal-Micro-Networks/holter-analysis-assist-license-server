from collections.abc import Mapping
from typing import Any

from flask import Response, jsonify

from license_server.domain.errors import ErrorCode

_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.LICENSE_INVALID: 401,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.LICENSE_SUSPENDED: 403,
    ErrorCode.MONTHLY_LIMIT_REACHED: 403,
    ErrorCode.LICENSE_NOT_FOUND: 404,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.TEMPORARY_FAILURE: 503,
}


def status_for(code: ErrorCode) -> int:
    return _STATUS[code]


def success_response(data: Mapping[str, Any], status: int = 200) -> Response:
    response = jsonify({"ok": True, "data": data})
    response.status_code = status
    return response


def error_response(code: ErrorCode, message: str, status: int | None = None) -> Response:
    response = jsonify({"ok": False, "error": {"code": code.value, "message": message}})
    response.status_code = status if status is not None else status_for(code)
    return response
