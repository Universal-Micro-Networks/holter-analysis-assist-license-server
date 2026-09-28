from typing import Any

import pytest

from license_server.domain.errors import ErrorCode, ServiceError
from license_server.http.rate_limit import WorkersRateLimiter, enforce_rate_limit


class CountingLimiter:
    def __init__(self, allow: int) -> None:
        self.allow = allow
        self.keys: list[str] = []

    def limit(self, key: str) -> bool:
        self.keys.append(key)
        return len(self.keys) <= self.allow


class BrokenLimiter:
    def limit(self, key: str) -> bool:
        raise RuntimeError("rate limit binding unavailable")


def test_allows_requests_under_the_limit() -> None:
    limiter = CountingLimiter(allow=2)
    enforce_rate_limit(limiter, "203.0.113.5")
    enforce_rate_limit(limiter, "203.0.113.5")
    assert limiter.keys == ["203.0.113.5", "203.0.113.5"]


def test_rejects_when_limit_is_exceeded() -> None:
    limiter = CountingLimiter(allow=1)
    enforce_rate_limit(limiter, "203.0.113.5")
    with pytest.raises(ServiceError) as caught:
        enforce_rate_limit(limiter, "203.0.113.5")
    assert caught.value.code is ErrorCode.RATE_LIMITED


def test_missing_source_ip_uses_a_shared_bucket() -> None:
    limiter = CountingLimiter(allow=5)
    enforce_rate_limit(limiter, None)
    assert limiter.keys == ["unknown"]


def test_unconfigured_limiter_does_not_block() -> None:
    enforce_rate_limit(None, "203.0.113.5")


def test_broken_limiter_fails_open() -> None:
    enforce_rate_limit(BrokenLimiter(), "203.0.113.5")


class FakeBinding:
    def __init__(self, success: bool) -> None:
        self.success = success
        self.calls: list[Any] = []

    def limit(self, options: Any) -> dict[str, bool]:
        self.calls.append(options)
        return {"success": self.success}


@pytest.mark.parametrize("success", [True, False])
def test_workers_adapter_passes_key_and_reads_success(success: bool) -> None:
    binding = FakeBinding(success)
    limiter = WorkersRateLimiter(binding, run_sync=lambda value: value)
    assert limiter.limit("203.0.113.5") is success
    assert binding.calls == [{"key": "203.0.113.5"}]
