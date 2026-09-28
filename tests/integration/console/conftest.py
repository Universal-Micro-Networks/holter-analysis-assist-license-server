import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from license_server.app import create_app
from license_server.console.access import AccessVerifier
from license_server.console.dependencies import ConsoleDependencies
from license_server.domain.types import Operator
from license_server.http.dependencies import Dependencies
from license_server.services.console_service import ConsoleService
from license_server.services.license_service import LicenseService
from license_server.services.usage_service import UsageService
from tests.conftest import FixedClock
from tests.fakes.access import AUDIENCE, TEAM_DOMAIN, FakeCertsFetcher, make_token
from tests.fakes.sqlite_repository import SqliteRepository
from tests.integration.conftest import CountingLimiter

HOST = "https://console.example.com"
SECRET = "console-test-session-secret-0123456789"
OPERATOR_EMAIL = "operator@example.com"


@dataclass
class ConsoleSetup:
    """Knobs for the console dependency factory; tests mutate these before making requests."""

    verifier: AccessVerifier | None
    dev_operator: Operator | None = None
    session_secret: str | None = SECRET
    limiter: CountingLimiter = field(default_factory=CountingLimiter)


@pytest.fixture
def fetcher() -> FakeCertsFetcher:
    return FakeCertsFetcher()


@pytest.fixture
def setup(fetcher: FakeCertsFetcher, clock: FixedClock) -> ConsoleSetup:
    return ConsoleSetup(verifier=AccessVerifier(TEAM_DOMAIN, AUDIENCE, fetcher, clock))


@pytest.fixture
def console_service(repo: SqliteRepository, clock: FixedClock) -> ConsoleService:
    return ConsoleService(repo, UsageService(repo, repo, clock), clock)


@pytest.fixture
def app(repo: SqliteRepository, clock: FixedClock, setup: ConsoleSetup, console_service: ConsoleService) -> Flask:
    def dependencies() -> Dependencies:
        return Dependencies(
            licenses=LicenseService(repo, clock),
            usage=UsageService(repo, repo, clock),
            rate_limiter=setup.limiter,
            admin_token="test-admin-token",
        )

    def console_dependencies() -> ConsoleDependencies:
        return ConsoleDependencies(
            console=console_service,
            verifier=setup.verifier,
            dev_operator=setup.dev_operator,
            session_secret=setup.session_secret,
            rate_limiter=setup.limiter,
            clock=clock,
        )

    return create_app(dependencies, console_dependencies)


class Browser:
    """A signed-in operator's browser: sends the Access assertion, keeps cookies, reads tokens from pages."""

    def __init__(self, client: FlaskClient, clock: FixedClock, host: str = HOST) -> None:
        self.client = client
        self.clock = clock
        self.host = host
        self.claims: dict[str, Any] = {}
        self.signed_in = True

    def token(self) -> str:
        now = int(self.clock.now.timestamp())
        return make_token(iat=now, nbf=now, exp=now + 3600, **self.claims)

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"CF-Connecting-IP": "203.0.113.7"}
        if self.signed_in:
            headers["Cf-Access-Jwt-Assertion"] = self.token()
        return {**headers, **(extra or {})}

    def get(self, path: str, **query: Any) -> TestResponse:
        return self.client.get(path, query_string=query or None, headers=self.headers(), base_url=self.host)

    def post(self, path: str, data: dict[str, str], origin: str | None = None, csrf: bool = True) -> TestResponse:
        form = dict(data)
        if csrf and "csrf_token" not in form:
            form["csrf_token"] = self.csrf_token()
        extra = {"Origin": origin or self.host}
        return self.client.post(path, data=form, headers=self.headers(extra), base_url=self.host)

    def csrf_token(self) -> str:
        return field_value(self.get("/console/licenses").get_data(as_text=True), "csrf_token")


def field_value(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]*)"', html)
    assert match, f"no {name} field in page"
    return match.group(1)


@pytest.fixture
def browser(client: FlaskClient, clock: FixedClock) -> Browser:
    return Browser(client, clock)


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def issue_via_console(browser: Browser, memo: str = "", monthly_limit: int | None = None) -> TestResponse:
    """Posts the issue form; the form has no limit field, so `monthly_limit` is sent only when given."""
    page = browser.get("/console/licenses/new").get_data(as_text=True)
    form = {"memo": memo, "request_id": field_value(page, "request_id")}
    if monthly_limit is not None:
        form["monthly_limit"] = str(monthly_limit)
    return browser.post("/console/licenses", form)


def detail_path(response: TestResponse) -> str:
    assert response.status_code == 302, response.get_data(as_text=True)
    location = response.headers["Location"]
    assert re.fullmatch(r"/console/licenses/lic_[0-9a-f]{16}", location), location
    return location


IssueFn = Callable[..., str]


@pytest.fixture
def issue(browser: Browser) -> IssueFn:
    def _issue(memo: str = "", monthly_limit: int | None = None) -> str:
        return detail_path(issue_via_console(browser, memo, monthly_limit))

    return _issue
