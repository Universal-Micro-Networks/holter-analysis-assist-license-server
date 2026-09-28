from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from flask import current_app, g

from license_server.console.access import AccessVerifier
from license_server.domain.types import Operator
from license_server.http.rate_limit import RateLimiter
from license_server.services.console_service import ConsoleService


@dataclass(frozen=True)
class ConsoleDependencies:
    console: ConsoleService
    verifier: AccessVerifier | None  # None until Cloudflare Access is configured
    # Local development only: used instead of Access when `verifier` is None and the host is loopback.
    dev_operator: Operator | None
    session_secret: str | None
    rate_limiter: RateLimiter | None
    clock: Callable[[], datetime]

    @property
    def configured(self) -> bool:
        return self.session_secret is not None and (self.verifier is not None or self.dev_operator is not None)


ConsoleDependencyFactory = Callable[[], ConsoleDependencies]

_EXTENSION_KEY = "license_server.console_dependencies"


def install(app_extensions: dict[str, object], factory: ConsoleDependencyFactory | None) -> None:
    app_extensions[_EXTENSION_KEY] = factory


def current_console() -> ConsoleDependencies:
    if "console_dependencies" not in g:
        factory = current_app.extensions.get(_EXTENSION_KEY)
        if factory is None:
            raise RuntimeError("no console dependency factory configured")
        g.console_dependencies = factory()
    deps: ConsoleDependencies = g.console_dependencies
    return deps
