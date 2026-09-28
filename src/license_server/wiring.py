"""Production dependency factories: the only module besides `app` that knows both the API and the console."""

from datetime import UTC, datetime

from flask import request

from license_server.config import ConsoleSettings, bindings_from_environ, console_settings_from_environ
from license_server.console.access import AccessVerifier, WorkersCertsFetcher
from license_server.console.dependencies import ConsoleDependencies
from license_server.domain.types import Operator
from license_server.http.dependencies import Dependencies
from license_server.http.rate_limit import WorkersRateLimiter
from license_server.repository.d1 import D1Repository
from license_server.services.console_service import ConsoleService
from license_server.services.license_service import LicenseService
from license_server.services.usage_service import UsageService

# Kept for the lifetime of the Worker instance so the Access signing keys are cached across requests.
_verifiers: dict[tuple[str, str], AccessVerifier] = {}


def workers_dependencies() -> Dependencies:
    bindings = bindings_from_environ(request.environ)
    repository = D1Repository(bindings.db)
    return Dependencies(
        licenses=LicenseService(repository, _utc_now),
        usage=UsageService(repository, repository, _utc_now),
        rate_limiter=WorkersRateLimiter(bindings.rate_limiter) if bindings.rate_limiter is not None else None,
        admin_token=bindings.admin_token,
    )


def workers_console_dependencies() -> ConsoleDependencies:
    bindings = bindings_from_environ(request.environ)
    settings = console_settings_from_environ(request.environ)
    repository = D1Repository(bindings.db)
    return ConsoleDependencies(
        console=ConsoleService(repository, UsageService(repository, repository, _utc_now), _utc_now),
        verifier=_verifier(settings),
        dev_operator=_dev_operator(settings),
        session_secret=settings.session_secret,
        rate_limiter=WorkersRateLimiter(bindings.rate_limiter) if bindings.rate_limiter is not None else None,
        clock=_utc_now,
    )


def _verifier(settings: ConsoleSettings) -> AccessVerifier | None:
    if settings.access_team_domain is None or settings.access_audience is None:
        return None
    cache_key = (settings.access_team_domain, settings.access_audience)
    if cache_key not in _verifiers:
        _verifiers[cache_key] = AccessVerifier(
            settings.access_team_domain, settings.access_audience, WorkersCertsFetcher(), _utc_now
        )
    return _verifiers[cache_key]


def _dev_operator(settings: ConsoleSettings) -> Operator | None:
    # Any Access setting means a real deployment; never fall back to the development operator there.
    if settings.dev_operator_email is None or settings.access_audience or settings.access_team_domain:
        return None
    return Operator(email=settings.dev_operator_email, subject=f"dev:{settings.dev_operator_email}")


def _utc_now() -> datetime:
    return datetime.now(UTC)
