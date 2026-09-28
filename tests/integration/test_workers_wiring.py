from license_server.app import create_app
from license_server.http.dependencies import current, workers_dependencies
from license_server.http.rate_limit import WorkersRateLimiter
from license_server.repository.d1 import D1Repository
from tests.fakes.fake_d1 import FakeD1Binding
from tests.unit.test_config import Env


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
