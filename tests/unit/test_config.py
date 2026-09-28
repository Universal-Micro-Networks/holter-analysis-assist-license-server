import pytest

from license_server.config import bindings_from_environ


class Env:
    def __init__(self, **bindings: object) -> None:
        for name, value in bindings.items():
            setattr(self, name, value)


def test_reads_bindings_and_admin_token_from_workers_env() -> None:
    db, limiter = object(), object()
    bindings = bindings_from_environ({"workers.env": Env(DB=db, RATE_LIMITER=limiter, ADMIN_API_TOKEN="secret")})
    assert (bindings.db, bindings.rate_limiter, bindings.admin_token) == (db, limiter, "secret")


def test_optional_bindings_may_be_absent() -> None:
    bindings = bindings_from_environ({"workers.env": Env(DB=object())})
    assert bindings.rate_limiter is None
    assert bindings.admin_token is None


@pytest.mark.parametrize("token", ["", 123, None])
def test_blank_or_non_string_admin_token_is_treated_as_unset(token: object) -> None:
    assert bindings_from_environ({"workers.env": Env(DB=object(), ADMIN_API_TOKEN=token)}).admin_token is None


def test_missing_workers_env_or_db_is_a_configuration_error() -> None:
    with pytest.raises(RuntimeError):
        bindings_from_environ({})
    with pytest.raises(RuntimeError):
        bindings_from_environ({"workers.env": Env()})
