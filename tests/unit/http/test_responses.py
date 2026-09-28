import pytest
from flask import Flask

from license_server.domain.errors import ErrorCode
from license_server.http.responses import error_response, status_for, success_response

DESIGNED_STATUS = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.LICENSE_INVALID: 401,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.LICENSE_SUSPENDED: 403,
    ErrorCode.MONTHLY_LIMIT_REACHED: 403,
    ErrorCode.LICENSE_NOT_FOUND: 404,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.TEMPORARY_FAILURE: 503,
}


@pytest.fixture
def app_context():
    with Flask(__name__).app_context() as context:
        yield context


def test_every_error_code_has_designed_http_status() -> None:
    assert {code: status_for(code) for code in ErrorCode} == DESIGNED_STATUS


def test_success_response_wraps_data(app_context) -> None:
    response = success_response({"status": "ok"}, status=201)
    assert response.status_code == 201
    assert response.get_json() == {"ok": True, "data": {"status": "ok"}}


def test_error_response_uses_code_status_and_message(app_context) -> None:
    response = error_response(ErrorCode.MONTHLY_LIMIT_REACHED, "limit reached")
    assert response.status_code == 403
    assert response.get_json() == {
        "ok": False,
        "error": {"code": "monthly_limit_reached", "message": "limit reached"},
    }


def test_error_response_status_can_be_overridden(app_context) -> None:
    response = error_response(ErrorCode.INVALID_REQUEST, "not found", status=404)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "invalid_request"
