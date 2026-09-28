import pytest
from flask import Flask
from flask.testing import FlaskClient

from license_server.app import create_app
from license_server.domain.errors import ErrorCode, ServiceError
from license_server.repository.base import RepositoryUnavailable

SECRET_DETAIL = "D1_ERROR: SELECT * FROM licenses WHERE license_key = 'lk_secret'"


@pytest.fixture
def app() -> Flask:
    app = create_app()

    @app.post("/__test/service-error")
    def raise_service_error():
        raise ServiceError(ErrorCode.LICENSE_SUSPENDED)

    @app.post("/__test/repository-unavailable")
    def raise_repository_unavailable():
        raise RepositoryUnavailable(SECRET_DETAIL)

    @app.post("/__test/unexpected")
    def raise_unexpected():
        raise RuntimeError(SECRET_DETAIL)

    @app.post("/__test/json")
    def read_json():
        from flask import request

        return {"ok": True, "data": request.get_json()}

    return app


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def assert_error_envelope(response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    assert SECRET_DETAIL not in response.get_data(as_text=True)


def test_healthz_returns_ok_envelope(client: FlaskClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "data": {"status": "ok"}}


def test_unknown_path_returns_error_envelope(client: FlaskClient) -> None:
    assert_error_envelope(client.get("/no-such-path"), 404, "invalid_request")


def test_disallowed_method_returns_error_envelope(client: FlaskClient) -> None:
    assert_error_envelope(client.post("/healthz"), 405, "invalid_request")


def test_malformed_json_returns_invalid_request(client: FlaskClient) -> None:
    response = client.post("/__test/json", data="{not json", content_type="application/json")
    assert_error_envelope(response, 400, "invalid_request")


def test_service_error_maps_to_its_code_and_status(client: FlaskClient) -> None:
    assert_error_envelope(client.post("/__test/service-error"), 403, "license_suspended")


def test_repository_failure_becomes_temporary_failure_without_details(client: FlaskClient) -> None:
    assert_error_envelope(client.post("/__test/repository-unavailable"), 503, "temporary_failure")


def test_unexpected_exception_becomes_temporary_failure_without_details(client: FlaskClient) -> None:
    assert_error_envelope(client.post("/__test/unexpected"), 503, "temporary_failure")
