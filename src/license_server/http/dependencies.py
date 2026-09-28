from collections.abc import Callable
from dataclasses import dataclass

from flask import current_app, g, request

from license_server.http.access_log import remember_license_key
from license_server.http.auth import extract_license_key, require_admin
from license_server.http.rate_limit import RateLimiter, enforce_rate_limit
from license_server.services.license_service import LicenseService
from license_server.services.usage_service import UsageService


@dataclass(frozen=True)
class Dependencies:
    licenses: LicenseService
    usage: UsageService
    rate_limiter: RateLimiter | None
    admin_token: str | None


DependencyFactory = Callable[[], Dependencies]

_EXTENSION_KEY = "license_server.dependencies"


def install(app_extensions: dict[str, object], factory: DependencyFactory | None) -> None:
    app_extensions[_EXTENSION_KEY] = factory


def current() -> Dependencies:
    # Bindings are only reachable inside a request on Workers, so build once per request.
    if "dependencies" not in g:
        factory = current_app.extensions.get(_EXTENSION_KEY)
        if factory is None:
            raise RuntimeError("no dependency factory configured")
        g.dependencies = factory()
    deps: Dependencies = g.dependencies
    return deps


def guard_client() -> tuple[Dependencies, str]:
    deps = current()
    enforce_rate_limit(deps.rate_limiter, request.headers.get("CF-Connecting-IP"))
    key = extract_license_key(request.headers.get("Authorization"))
    remember_license_key(key)
    return deps, key


def guard_admin() -> Dependencies:
    deps = current()
    enforce_rate_limit(deps.rate_limiter, request.headers.get("CF-Connecting-IP"))
    require_admin(request.headers.get("Authorization"), deps.admin_token)
    return deps
