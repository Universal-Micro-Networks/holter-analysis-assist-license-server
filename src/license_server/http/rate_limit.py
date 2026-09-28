import logging
from collections.abc import Callable
from typing import Any, Protocol

from license_server.domain.errors import ErrorCode, ServiceError

logger = logging.getLogger(__name__)


class RateLimiter(Protocol):
    def limit(self, key: str) -> bool: ...


def _pyodide_run_sync(awaitable: Any) -> Any:
    from pyodide.ffi import run_sync

    return run_sync(awaitable)


class WorkersRateLimiter:
    """Adapter for the Workers Rate Limiting binding (approximate, per Cloudflare location)."""

    def __init__(self, binding: Any, run_sync: Callable[[Any], Any] = _pyodide_run_sync) -> None:
        self._binding = binding
        self._run_sync = run_sync

    def limit(self, key: str) -> bool:
        outcome = self._run_sync(self._binding.limit({"key": key}))
        return bool(outcome["success"])


def enforce_rate_limit(limiter: RateLimiter | None, source_ip: str | None) -> None:
    # The binding cannot peek without consuming, so every request counts, not only ones with invalid keys.
    if limiter is None:
        return
    try:
        allowed = limiter.limit(source_ip or "unknown")
    except Exception as error:
        # Availability first: a broken limiter must not take the service down.
        logger.warning("rate limiter unavailable, allowing request: %s", type(error).__name__)
        return
    if not allowed:
        raise ServiceError(ErrorCode.RATE_LIMITED)
