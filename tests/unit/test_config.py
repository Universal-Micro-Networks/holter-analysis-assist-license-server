import pytest

from license_server.config import ConsoleSettings, bindings_from_environ, console_settings_from_environ


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


class TestConsoleSettings:
    def test_reads_access_session_and_dev_operator_settings(self) -> None:
        settings = console_settings_from_environ(
            {
                "workers.env": Env(
                    ACCESS_TEAM_DOMAIN="https://example.cloudflareaccess.com",
                    ACCESS_AUD="aud-tag",
                    CONSOLE_SESSION_SECRET="s" * 32,
                    CONSOLE_DEV_OPERATOR_EMAIL="dev@example.com",
                )
            }
        )
        assert settings == ConsoleSettings(
            access_team_domain="https://example.cloudflareaccess.com",
            access_audience="aud-tag",
            session_secret="s" * 32,
            dev_operator_email="dev@example.com",
        )

    def test_all_settings_are_optional(self) -> None:
        settings = console_settings_from_environ({"workers.env": Env()})
        assert settings == ConsoleSettings(None, None, None, None)
        assert settings.access_configured is False

    @pytest.mark.parametrize(
        "value", ["example.cloudflareaccess.com", "https://example.cloudflareaccess.com/", " https://example.cloudflareaccess.com "]
    )
    def test_team_domain_is_normalized_to_https_origin(self, value: str) -> None:
        settings = console_settings_from_environ({"workers.env": Env(ACCESS_TEAM_DOMAIN=value)})
        assert settings.access_team_domain == "https://example.cloudflareaccess.com"

    @pytest.mark.parametrize("value", ["http://example.cloudflareaccess.com", "https://a/b", "https://"])
    def test_team_domain_other_than_https_host_is_treated_as_unset(self, value: str) -> None:
        assert console_settings_from_environ({"workers.env": Env(ACCESS_TEAM_DOMAIN=value)}).access_team_domain is None

    @pytest.mark.parametrize("name", ["ACCESS_AUD", "CONSOLE_SESSION_SECRET", "CONSOLE_DEV_OPERATOR_EMAIL"])
    @pytest.mark.parametrize("value", ["", "   ", 123, None])
    def test_blank_or_non_string_values_are_treated_as_unset(self, name: str, value: object) -> None:
        settings = console_settings_from_environ({"workers.env": Env(**{name: value})})
        assert settings == ConsoleSettings(None, None, None, None)

    def test_access_is_configured_only_with_both_team_domain_and_audience(self) -> None:
        def configured(**values: object) -> bool:
            return console_settings_from_environ({"workers.env": Env(**values)}).access_configured

        team = "https://example.cloudflareaccess.com"
        assert configured(ACCESS_TEAM_DOMAIN=team, ACCESS_AUD="aud") is True
        assert configured(ACCESS_TEAM_DOMAIN=team) is False
        assert configured(ACCESS_AUD="aud") is False

    def test_missing_workers_env_is_a_configuration_error(self) -> None:
        with pytest.raises(RuntimeError):
            console_settings_from_environ({})
