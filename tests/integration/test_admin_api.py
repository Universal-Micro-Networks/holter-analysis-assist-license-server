from collections.abc import Callable

import pytest
from flask.testing import FlaskClient

from license_server.domain.license_key import is_valid_license_key
from tests.conftest import FixedClock
from tests.integration.conftest import ADMIN, bearer

UNKNOWN = "lk_" + "f" * 32
ADMIN_PATHS = [
    "/v1/admin/licenses",
    "/v1/admin/licenses/get",
    "/v1/admin/licenses/update-limit",
    "/v1/admin/licenses/suspend",
    "/v1/admin/licenses/activate",
    "/v1/admin/usage/logs",
    "/v1/admin/usage/monthly",
]


def error_of(response) -> tuple[int, str]:
    body = response.get_json()
    assert body["ok"] is False
    return response.status_code, body["error"]["code"]


@pytest.mark.parametrize("path", ADMIN_PATHS)
@pytest.mark.parametrize("headers", [{}, bearer("wrong-token")])
def test_every_admin_endpoint_requires_admin_token(client: FlaskClient, path: str, headers: dict[str, str]) -> None:
    assert error_of(client.post(path, json={}, headers=headers)) == (401, "unauthorized")


@pytest.mark.parametrize("path", ADMIN_PATHS[1:])
def test_admin_endpoints_on_unknown_license_are_not_found(client: FlaskClient, path: str) -> None:
    body = {"license_key": UNKNOWN, "monthly_limit": 5, "from": "2026-09-01T00:00:00Z", "to": "2026-10-01T00:00:00Z", "month": "2026-09"}
    assert error_of(client.post(path, json=body, headers=ADMIN)) == (404, "license_not_found")


class TestLicenses:
    def test_issue_returns_new_license(self, client: FlaskClient) -> None:
        response = client.post("/v1/admin/licenses", json={"monthly_limit": 50}, headers=ADMIN)
        assert response.status_code == 201
        data = response.get_json()["data"]
        assert is_valid_license_key(data["license_key"])
        assert data | {"license_key": "x"} == {
            "license_key": "x",
            "monthly_limit": 50,
            "status": "active",
            "created_at": "2026-09-15T03:00:00.000Z",
            "updated_at": "2026-09-15T03:00:00.000Z",
        }

    def test_license_responses_do_not_expose_console_fields(self, client: FlaskClient) -> None:
        data = client.post("/v1/admin/licenses", json={"monthly_limit": 5}, headers=ADMIN).get_json()["data"]
        assert set(data) == {"license_key", "monthly_limit", "status", "created_at", "updated_at"}

    @pytest.mark.parametrize("body", [{}, {"monthly_limit": -1}, {"monthly_limit": "10"}, {"monthly_limit": 1.5}, [1]])
    def test_issue_validates_monthly_limit(self, client: FlaskClient, body: object) -> None:
        assert error_of(client.post("/v1/admin/licenses", json=body, headers=ADMIN)) == (400, "invalid_request")

    def test_non_json_body_is_invalid_request(self, client: FlaskClient) -> None:
        response = client.post("/v1/admin/licenses", data="monthly_limit=5", headers=ADMIN)
        assert error_of(response) == (400, "invalid_request")

    def test_get_update_suspend_activate(
        self, client: FlaskClient, issue: Callable[[int], str], clock: FixedClock
    ) -> None:
        key = issue(10)
        assert client.post("/v1/admin/licenses/get", json={"license_key": key}, headers=ADMIN).get_json()["data"]["monthly_limit"] == 10
        clock.advance(hours=1)
        updated = client.post("/v1/admin/licenses/update-limit", json={"license_key": key, "monthly_limit": 20}, headers=ADMIN)
        assert (updated.status_code, updated.get_json()["data"]["monthly_limit"]) == (200, 20)
        assert updated.get_json()["data"]["updated_at"] == "2026-09-15T04:00:00.000Z"
        suspended = client.post("/v1/admin/licenses/suspend", json={"license_key": key}, headers=ADMIN)
        assert suspended.get_json()["data"]["status"] == "suspended"
        activated = client.post("/v1/admin/licenses/activate", json={"license_key": key}, headers=ADMIN)
        assert activated.get_json()["data"]["status"] == "active"

    @pytest.mark.parametrize("key", [None, "", "lk_short", 123])
    def test_malformed_license_key_is_invalid_request(self, client: FlaskClient, key: object) -> None:
        response = client.post("/v1/admin/licenses/get", json={"license_key": key}, headers=ADMIN)
        assert error_of(response) == (400, "invalid_request")

    def test_license_key_is_never_taken_from_the_url(self, client: FlaskClient, issue: Callable[[int], str]) -> None:
        key = issue(10)
        response = client.post(f"/v1/admin/licenses/get?license_key={key}", json={}, headers=ADMIN)
        assert error_of(response) == (400, "invalid_request")


class TestUsageQueries:
    def test_logs_and_monthly_count(self, client: FlaskClient, issue: Callable[[int], str], clock: FixedClock) -> None:
        key = issue(10)
        for _ in range(3):
            client.post("/v1/usage", headers=bearer(key))
            clock.advance(minutes=1)
        body = {"license_key": key, "from": "2026-09-15T12:00:00+09:00", "to": "2026-09-16T00:00:00+09:00", "limit": 2}
        first = client.post("/v1/admin/usage/logs", json=body, headers=ADMIN).get_json()["data"]
        assert first == {
            "entries": [{"id": 1, "used_at": "2026-09-15T03:00:00.000Z"}, {"id": 2, "used_at": "2026-09-15T03:01:00.000Z"}],
            "next_after_id": 2,
        }
        second = client.post("/v1/admin/usage/logs", json=body | {"after_id": 2}, headers=ADMIN).get_json()["data"]
        assert second == {"entries": [{"id": 3, "used_at": "2026-09-15T03:02:00.000Z"}], "next_after_id": None}
        monthly = client.post("/v1/admin/usage/monthly", json={"license_key": key, "month": "2026-09"}, headers=ADMIN)
        assert monthly.get_json()["data"]["used"] == 3

    @pytest.mark.parametrize(
        "override",
        [
            {"from": "2026-09-01T00:00:00"},
            {"to": "not-a-date"},
            {"from": "2026-10-01T00:00:00Z", "to": "2026-09-01T00:00:00Z"},
            {"limit": 0},
            {"limit": 1001},
            {"limit": "10"},
            {"after_id": -1},
            {"after_id": "1"},
        ],
    )
    def test_logs_validate_input(self, client: FlaskClient, issue: Callable[[int], str], override: dict) -> None:
        body = {"license_key": issue(10), "from": "2026-09-01T00:00:00Z", "to": "2026-10-01T00:00:00Z"} | override
        assert error_of(client.post("/v1/admin/usage/logs", json=body, headers=ADMIN)) == (400, "invalid_request")

    @pytest.mark.parametrize("month", ["2026-9", "2026-13", None, 202609])
    def test_monthly_validates_month(self, client: FlaskClient, issue: Callable[[int], str], month: object) -> None:
        body = {"license_key": issue(10), "month": month}
        assert error_of(client.post("/v1/admin/usage/monthly", json=body, headers=ADMIN)) == (400, "invalid_request")
