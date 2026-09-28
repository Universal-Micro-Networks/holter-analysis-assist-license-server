from collections.abc import Callable

import pytest
from flask.testing import FlaskClient

from tests.fakes.sqlite_repository import SqliteRepository
from tests.integration.conftest import ADMIN, CountingLimiter, bearer

UNKNOWN = "lk_" + "f" * 32
SEPTEMBER_PERIOD = {"start": "2026-08-31T15:00:00.000Z", "end": "2026-09-30T15:00:00.000Z", "timezone": "Asia/Tokyo"}


def error_of(response) -> tuple[int, str]:
    body = response.get_json()
    assert body["ok"] is False
    return response.status_code, body["error"]["code"]


class TestVerify:
    def test_valid_license_returns_limit_and_status(self, client: FlaskClient, issue: Callable[[int], str]) -> None:
        key = issue(100)
        response = client.post("/v1/licenses/verify", headers=bearer(key))
        assert response.status_code == 200
        assert response.get_json() == {"ok": True, "data": {"valid": True, "monthly_limit": 100, "status": "active"}}

    def test_unknown_key_is_license_invalid_with_fixed_message(self, client: FlaskClient) -> None:
        response = client.post("/v1/licenses/verify", headers=bearer(UNKNOWN))
        assert error_of(response) == (401, "license_invalid")
        assert response.get_json()["error"]["message"] == "The license key is not valid."
        assert UNKNOWN not in response.get_data(as_text=True)

    def test_suspended_license_is_rejected(self, client: FlaskClient, issue: Callable[[int], str]) -> None:
        key = issue(100)
        client.post("/v1/admin/licenses/suspend", json={"license_key": key}, headers=ADMIN)
        assert error_of(client.post("/v1/licenses/verify", headers=bearer(key))) == (403, "license_suspended")

    @pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}])
    def test_missing_or_malformed_key_is_invalid_request(self, client: FlaskClient, headers: dict[str, str]) -> None:
        assert error_of(client.post("/v1/licenses/verify", headers=headers)) == (400, "invalid_request")


class TestRecordUsage:
    def test_records_and_returns_summary(self, client: FlaskClient, issue: Callable[[int], str]) -> None:
        key = issue(3)
        response = client.post("/v1/usage", headers=bearer(key))
        assert response.status_code == 201
        assert response.get_json() == {
            "ok": True,
            "data": {"allowed": True, "used": 1, "monthly_limit": 3, "remaining": 2, "period": SEPTEMBER_PERIOD},
        }

    def test_limit_reached_after_n_records(
        self, client: FlaskClient, issue: Callable[[int], str], repo: SqliteRepository
    ) -> None:
        key = issue(2)
        assert [client.post("/v1/usage", headers=bearer(key)).status_code for _ in range(2)] == [201, 201]
        assert error_of(client.post("/v1/usage", headers=bearer(key))) == (403, "monthly_limit_reached")

    def test_request_body_is_ignored_and_not_stored(
        self, client: FlaskClient, issue: Callable[[int], str], repo: SqliteRepository
    ) -> None:
        key = issue(3)
        response = client.post("/v1/usage", headers=bearer(key), json={"ecg": [1, 2, 3], "used_at": "2000-01-01T00:00:00Z"})
        assert response.status_code == 201
        [entry] = repo.list_in_range(key, SEPTEMBER_PERIOD["start"], SEPTEMBER_PERIOD["end"], None, 10)
        assert entry.used_at == "2026-09-15T03:00:00.000Z"

    def test_unknown_and_suspended_licenses_are_rejected(self, client: FlaskClient, issue: Callable[[int], str]) -> None:
        assert error_of(client.post("/v1/usage", headers=bearer(UNKNOWN))) == (401, "license_invalid")
        key = issue(3)
        client.post("/v1/admin/licenses/suspend", json={"license_key": key}, headers=ADMIN)
        assert error_of(client.post("/v1/usage", headers=bearer(key))) == (403, "license_suspended")

    def test_get_is_not_allowed(self, client: FlaskClient) -> None:
        assert client.get("/v1/usage").status_code == 405


class TestCurrentUsage:
    def test_returns_summary_without_recording(self, client: FlaskClient, issue: Callable[[int], str]) -> None:
        key = issue(3)
        client.post("/v1/usage", headers=bearer(key))
        for _ in range(2):
            response = client.get("/v1/usage/current", headers=bearer(key))
            assert response.status_code == 200
            assert response.get_json()["data"] == {"used": 1, "monthly_limit": 3, "remaining": 2, "period": SEPTEMBER_PERIOD}

    def test_unknown_license_is_rejected(self, client: FlaskClient) -> None:
        assert error_of(client.get("/v1/usage/current", headers=bearer(UNKNOWN))) == (401, "license_invalid")


class TestRateLimit:
    def test_rate_limit_applies_before_authentication(self, client: FlaskClient, limiter: CountingLimiter) -> None:
        limiter.allow = 0
        assert error_of(client.post("/v1/licenses/verify", headers=bearer(UNKNOWN))) == (429, "rate_limited")
        assert limiter.keys == ["203.0.113.5"]

    def test_healthz_is_not_rate_limited(self, client: FlaskClient, limiter: CountingLimiter) -> None:
        limiter.allow = 0
        assert client.get("/healthz").status_code == 200
        assert limiter.keys == []


def test_no_endpoint_modifies_or_deletes_usage(client: FlaskClient, issue: Callable[[int], str]) -> None:
    key = issue(3)
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)("/v1/usage", headers=bearer(key)).status_code == 405
