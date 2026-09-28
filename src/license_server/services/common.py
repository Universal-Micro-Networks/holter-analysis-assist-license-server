from collections.abc import Callable
from datetime import datetime

from license_server.domain.errors import ErrorCode, ServiceError
from license_server.domain.types import License, LicenseStatus

Clock = Callable[[], datetime]


def ensure_active(license_: License | None) -> License:
    """Client-facing precondition: unknown keys and suspended licenses are rejected with distinct codes."""
    if license_ is None:
        raise ServiceError(ErrorCode.LICENSE_INVALID)
    if license_.status is LicenseStatus.SUSPENDED:
        raise ServiceError(ErrorCode.LICENSE_SUSPENDED)
    return license_


def ensure_exists(license_: License | None) -> License:
    """Admin-facing precondition: the target license must exist regardless of its status."""
    if license_ is None:
        raise ServiceError(ErrorCode.LICENSE_NOT_FOUND)
    return license_
