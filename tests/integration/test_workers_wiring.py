from license_server.app import create_app
from license_server.console.access import AccessVerifier
from license_server.console.dependencies import current_console
from license_server.domain.types import Operator
from license_server.http.dependencies import current
from license_server.http.rate_limit import WorkersRateLimiter
from license_server.repository.d1 import D1Repository
from license_server.wiring import workers_console_dependencies, workers_dependencies
from tests.fakes.fake_d1 import FakeD1Binding
from tests.unit.test_config import Env

TEAM = "https://example.cloudflareaccess.com"


def test_builds_d1_backed_services_and_limiter_per_request() -> None:
    app = create_app(workers_dependencies)
    env = Env(DB=FakeD1Binding(), RATE_LIMITER=object(), ADMIN_API_TOKEN="secret")
    with app.test_request_context(environ_base={"workers.env": env}):
        deps = current()
        assert isinstance(deps.licenses._licenses, D1Repository)
        assert isinstance(deps.usage._usage, D1Repository)
        assert isinstance(deps.rate_limiter, WorkersRateLimiter)
        assert deps.admin_token == "secret"
        assert current() is deps


def test_without_rate_limiter_binding_no_limiter_is_used() -> None:
    app = create_app(workers_dependencies)
    with app.test_request_context(environ_base={"workers.env": Env(DB=FakeD1Binding())}):
        assert current().rate_limiter is None


def test_missing_bindings_surface_as_temporary_failure() -> None:
    response = create_app(workers_dependencies).test_client().post("/v1/licenses/verify")
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "temporary_failure"


class TestConsoleWiring:
    def console_deps(self, **env: object):  # type: ignore[no-untyped-def]
        app = create_app(workers_dependencies, workers_console_dependencies)
        with app.test_request_context(environ_base={"workers.env": Env(DB=FakeD1Binding(), **env)}):
            return current_console()

    def test_access_settings_build_a_verifier_and_disable_dev_operator(self) -> None:
        deps = self.console_deps(
            ACCESS_TEAM_DOMAIN=TEAM, ACCESS_AUD="aud", CONSOLE_SESSION_SECRET="s" * 32,
            CONSOLE_DEV_OPERATOR_EMAIL="dev@example.com", RATE_LIMITER=object(),
        )
        assert isinstance(deps.verifier, AccessVerifier)
        assert deps.dev_operator is None
        assert deps.session_secret == "s" * 32
        assert isinstance(deps.rate_limiter, WorkersRateLimiter)
        assert isinstance(deps.console._repo, D1Repository)
        assert deps.configured

    def test_audience_alone_still_disables_dev_operator(self) -> None:
        deps = self.console_deps(ACCESS_AUD="aud", CONSOLE_DEV_OPERATOR_EMAIL="dev@example.com")
        assert deps.verifier is None and deps.dev_operator is None
        assert not deps.configured

    def test_dev_operator_only_without_access_settings(self) -> None:
        deps = self.console_deps(CONSOLE_DEV_OPERATOR_EMAIL="dev@example.com", CONSOLE_SESSION_SECRET="s" * 32)
        assert deps.verifier is None
        assert deps.dev_operator == Operator("dev@example.com", "dev:dev@example.com")
        assert deps.configured

    def test_nothing_configured_fails_closed(self) -> None:
        deps = self.console_deps()
        assert (deps.verifier, deps.dev_operator, deps.session_secret) == (None, None, None)
        assert not deps.configured

    def test_verifier_and_its_key_cache_survive_across_requests(self) -> None:
        settings = {"ACCESS_TEAM_DOMAIN": TEAM, "ACCESS_AUD": "aud"}
        assert self.console_deps(**settings).verifier is self.console_deps(**settings).verifier
        assert self.console_deps(**settings).verifier is not self.console_deps(ACCESS_TEAM_DOMAIN=TEAM, ACCESS_AUD="other").verifier

    def test_unconfigured_console_answers_503_html(self) -> None:
        app = create_app(workers_dependencies, workers_console_dependencies)
        response = app.test_client().get(
            "/console/licenses", environ_base={"workers.env": Env(DB=FakeD1Binding())}, base_url="https://console.example.com"
        )
        assert response.status_code == 503
        assert "管理画面が設定されていません" in response.get_data(as_text=True)
