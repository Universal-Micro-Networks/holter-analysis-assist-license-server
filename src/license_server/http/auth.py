import hmac

from license_server.domain.errors import ErrorCode, ServiceError
from license_server.domain.license_key import is_valid_license_key


def _bearer_token(authorization: str | None) -> str | None:
    parts = (authorization or "").strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def extract_license_key(authorization: str | None) -> str:
    key = _bearer_token(authorization)
    if key is None or not is_valid_license_key(key):
        raise ServiceError(ErrorCode.INVALID_REQUEST, "Authorization header must be 'Bearer <license key>'.")
    return key


def require_admin(authorization: str | None, expected_token: str | None) -> None:
    # Fail closed: without a configured token the admin API is unreachable.
    token = _bearer_token(authorization)
    if not expected_token or token is None:
        raise ServiceError(ErrorCode.UNAUTHORIZED)
    if not hmac.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8")):
        raise ServiceError(ErrorCode.UNAUTHORIZED)
