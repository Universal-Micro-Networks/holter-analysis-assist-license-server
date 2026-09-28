from collections.abc import Callable

import pytest
from flask import Flask
from flask.testing import FlaskClient

from license_server.app import create_app
from license_server.http.dependencies import Dependencies
from license_server.services.license_service import LicenseService
from license_server.services.usage_service import UsageService
from tests.conftest import FixedClock
from tests.fakes.sqlite_repository import SqliteRepository

ADMIN_TOKEN = "test-admin-token"


class CountingLimiter:
    def __init__(self, allow: int = 1_000_000) -> None:
        self.allow = allow
        self.keys: list[str] = []

    def limit(self, key: str) -> bool:
        self.keys.append(key)
        return len(self.keys) <= self.allow


@pytest.fixture
def limiter() -> CountingLimiter:
    return CountingLimiter()


@pytest.fixture
def app(repo: SqliteRepository, clock: FixedClock, limiter: CountingLimiter) -> Flask:
    def dependencies() -> Dependencies:
        return Dependencies(
            licenses=LicenseService(repo, clock),
            usage=UsageService(repo, repo, clock),
            rate_limiter=limiter,
            admin_token=ADMIN_TOKEN,
        )

    return create_app(dependencies)


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "CF-Connecting-IP": "203.0.113.5"}


ADMIN = bearer(ADMIN_TOKEN)


@pytest.fixture
def issue(client: FlaskClient) -> Callable[[int], str]:
    def _issue(monthly_limit: int) -> str:
        response = client.post("/v1/admin/licenses", json={"monthly_limit": monthly_limit}, headers=ADMIN)
        assert response.status_code == 201, response.get_json()
        return str(response.get_json()["data"]["license_key"])

    return _issue
