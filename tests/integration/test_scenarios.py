from collections.abc import Callable

import pytest
from flask.testing import FlaskClient

from license_server.domain.errors import ErrorCode
from license_server.domain.period import month_period
from tests.conftest import FixedClock
from tests.fakes.sqlite_repository import SqliteRepository
from tests.integration.conftest import ADMIN, CountingLimiter, bearer

UNKNOWN = "lk_" + "f" * 32


def record(client: FlaskClient, key: str) -> tuple[int, str | None]:
    response = client.post("/v1/usage", headers=bearer(key))
    body = response.get_json()
    return response.status_code, None if body["ok"] else body["error"]["code"]


def logged(repo: SqliteRepository, clock: FixedClock, key: str) -> int:
    return repo.count_in_period(key, month_period(clock()))


@pytest.mark.parametrize("limit", [1, 5])
def test_limit_n_allows_exactly_n_records(
    client: FlaskClient, issue: Callable[[int], str], repo: SqliteRepository, clock: FixedClock, limit: int
) -> None:
    key = issue(limit)
    assert [record(client, key) for _ in range(limit)] == [(201, None)] * limit
    assert record(client, key) == (403, "monthly_limit_reached")
    assert logged(repo, clock, key) == limit


def test_limit_zero_is_unlimited_and_reports_no_remaining(
    client: FlaskClient, issue: Callable[[int], str], repo: SqliteRepository, clock: FixedClock
) -> None:
    key = issue(0)
    responses = [client.post("/v1/usage", headers=bearer(key)) for _ in range(3)]
    assert [r.status_code for r in responses] == [201] * 3
    assert {k: responses[-1].get_json()["data"][k] for k in ("used", "monthly_limit", "remaining")} == {
        "used": 3,
        "monthly_limit": 0,
        "remaining": None,
    }
    assert logged(repo, clock, key) == 3


def test_suspend_reject_activate_keeps_history(
    client: FlaskClient, issue: Callable[[int], str], repo: SqliteRepository, clock: FixedClock
) -> None:
    key = issue(10)
    assert record(client, key) == (201, None)
    client.post("/v1/admin/licenses/suspend", json={"license_key": key}, headers=ADMIN)
    assert record(client, key) == (403, "license_suspended")
    assert logged(repo, clock, key) == 1
    client.post("/v1/admin/licenses/activate", json={"license_key": key}, headers=ADMIN)
    assert record(client, key) == (201, None)
    assert logged(repo, clock, key) == 2


def test_limit_change_applies_to_the_next_decision(client: FlaskClient, issue: Callable[[int], str]) -> None:
    key = issue(1)
    assert record(client, key) == (201, None)
    assert record(client, key) == (403, "monthly_limit_reached")
    client.post("/v1/admin/licenses/update-limit", json={"license_key": key, "monthly_limit": 2}, headers=ADMIN)
    assert record(client, key) == (201, None)
    client.post("/v1/admin/licenses/update-limit", json={"license_key": key, "monthly_limit": 1}, headers=ADMIN)
    assert record(client, key) == (403, "monthly_limit_reached")
    client.post("/v1/admin/licenses/update-limit", json={"license_key": key, "monthly_limit": 0}, headers=ADMIN)
    assert record(client, key) == (201, None)


def test_new_month_resets_the_count(client: FlaskClient, issue: Callable[[int], str], clock: FixedClock) -> None:
    key = issue(1)
    assert record(client, key) == (201, None)
    clock.advance(days=16)
    assert record(client, key) == (201, None)


def test_storage_outage_denies_usage_without_leaking_details(
    client: FlaskClient, issue: Callable[[int], str], repo: SqliteRepository, clock: FixedClock
) -> None:
    key = issue(10)
    repo.unavailable = True
    for request in (
        lambda: client.post("/v1/usage", headers=bearer(key)),
        lambda: client.post("/v1/licenses/verify", headers=bearer(key)),
        lambda: client.post("/v1/admin/licenses/get", json={"license_key": key}, headers=ADMIN),
    ):
        response = request()
        assert response.status_code == 503
        assert response.get_json() == {
            "ok": False,
            "error": {"code": "temporary_failure", "message": "The service is temporarily unavailable. Please retry later."},
        }
    repo.unavailable = False
    assert logged(repo, clock, key) == 0


def test_every_error_kind_is_distinct_and_uses_the_common_envelope(
    client: FlaskClient, issue: Callable[[int], str], repo: SqliteRepository, limiter: CountingLimiter
) -> None:
    suspended, exhausted = issue(5), issue(1)
    client.post("/v1/admin/licenses/suspend", json={"license_key": suspended}, headers=ADMIN)
    assert record(client, exhausted) == (201, None)

    def outage():
        repo.unavailable = True
        try:
            return client.post("/v1/usage", headers=bearer(exhausted))
        finally:
            repo.unavailable = False

    def throttled():
        limiter.allow = len(limiter.keys)
        return client.post("/v1/usage", headers=bearer(exhausted))

    responses = {
        ErrorCode.INVALID_REQUEST: client.post("/v1/usage", headers={"Authorization": "Bearer bad"}),
        ErrorCode.LICENSE_INVALID: client.post("/v1/usage", headers=bearer(UNKNOWN)),
        ErrorCode.UNAUTHORIZED: client.post("/v1/admin/licenses", json={}, headers=bearer("wrong")),
        ErrorCode.LICENSE_SUSPENDED: client.post("/v1/usage", headers=bearer(suspended)),
        ErrorCode.MONTHLY_LIMIT_REACHED: client.post("/v1/usage", headers=bearer(exhausted)),
        ErrorCode.LICENSE_NOT_FOUND: client.post("/v1/admin/licenses/get", json={"license_key": UNKNOWN}, headers=ADMIN),
        ErrorCode.TEMPORARY_FAILURE: outage(),
        ErrorCode.RATE_LIMITED: throttled(),
    }
    assert set(responses) == set(ErrorCode)
    for code, response in responses.items():
        body = response.get_json()
        assert set(body) == {"ok", "error"} and body["ok"] is False
        assert set(body["error"]) == {"code", "message"}
        assert body["error"]["code"] == code.value
        assert body["error"]["message"]
    statuses = {code: response.status_code for code, response in responses.items()}
    assert statuses == {
        ErrorCode.INVALID_REQUEST: 400,
        ErrorCode.LICENSE_INVALID: 401,
        ErrorCode.UNAUTHORIZED: 401,
        ErrorCode.LICENSE_SUSPENDED: 403,
        ErrorCode.MONTHLY_LIMIT_REACHED: 403,
        ErrorCode.LICENSE_NOT_FOUND: 404,
        ErrorCode.TEMPORARY_FAILURE: 503,
        ErrorCode.RATE_LIMITED: 429,
    }
