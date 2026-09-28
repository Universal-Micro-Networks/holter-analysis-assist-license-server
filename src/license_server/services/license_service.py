from collections.abc import Callable

from license_server.domain.errors import ErrorCode, ServiceError
from license_server.domain.license_key import generate_license_key
from license_server.domain.period import to_utc_text
from license_server.domain.types import License, LicenseStatus
from license_server.repository.base import DuplicateLicenseKey, LicenseRepository, RepositoryUnavailable
from license_server.services.common import Clock, ensure_active, ensure_exists

MAX_MONTHLY_LIMIT = 1_000_000
_ISSUE_ATTEMPTS = 3


def validate_monthly_limit(value: object) -> int:
    # bool is a subclass of int, so it has to be excluded explicitly.
    if type(value) is not int or not 0 <= value <= MAX_MONTHLY_LIMIT:
        raise ServiceError(
            ErrorCode.INVALID_REQUEST, f"monthly_limit must be an integer between 0 and {MAX_MONTHLY_LIMIT}."
        )
    return value


class LicenseService:
    def __init__(
        self,
        licenses: LicenseRepository,
        clock: Clock,
        key_generator: Callable[[], str] = generate_license_key,
    ) -> None:
        self._licenses = licenses
        self._clock = clock
        self._key_generator = key_generator

    def require_active(self, license_key: str) -> License:
        return ensure_active(self._licenses.find(license_key))

    def get(self, license_key: str) -> License:
        return ensure_exists(self._licenses.find(license_key))

    def issue(self, monthly_limit: object) -> License:
        limit = validate_monthly_limit(monthly_limit)
        for _ in range(_ISSUE_ATTEMPTS):
            try:
                return self._licenses.create(self._key_generator(), limit, self._now())
            except DuplicateLicenseKey:
                continue
        raise RepositoryUnavailable(f"could not generate a unique license key in {_ISSUE_ATTEMPTS} attempts")

    def update_limit(self, license_key: str, monthly_limit: object) -> License:
        limit = validate_monthly_limit(monthly_limit)
        return ensure_exists(self._licenses.update_limit(license_key, limit, self._now()))

    def suspend(self, license_key: str) -> License:
        return ensure_exists(self._licenses.set_status(license_key, LicenseStatus.SUSPENDED, self._now()))

    def activate(self, license_key: str) -> License:
        return ensure_exists(self._licenses.set_status(license_key, LicenseStatus.ACTIVE, self._now()))

    def _now(self) -> str:
        return to_utc_text(self._clock())
