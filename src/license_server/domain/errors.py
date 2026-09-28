from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    LICENSE_INVALID = "license_invalid"
    LICENSE_SUSPENDED = "license_suspended"
    MONTHLY_LIMIT_REACHED = "monthly_limit_reached"
    UNAUTHORIZED = "unauthorized"
    LICENSE_NOT_FOUND = "license_not_found"
    RATE_LIMITED = "rate_limited"
    TEMPORARY_FAILURE = "temporary_failure"


# Fixed wording: responses must not reveal whether other licenses exist or any internal detail.
_DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_REQUEST: "The request is malformed.",
    ErrorCode.LICENSE_INVALID: "The license key is not valid.",
    ErrorCode.LICENSE_SUSPENDED: "The license is suspended.",
    ErrorCode.MONTHLY_LIMIT_REACHED: "The monthly usage limit has been reached.",
    ErrorCode.UNAUTHORIZED: "Authentication is required.",
    ErrorCode.LICENSE_NOT_FOUND: "The license does not exist.",
    ErrorCode.RATE_LIMITED: "Too many requests. Please retry later.",
    ErrorCode.TEMPORARY_FAILURE: "The service is temporarily unavailable. Please retry later.",
}


class ServiceError(Exception):
    def __init__(self, code: ErrorCode, message: str | None = None) -> None:
        self.code = code
        self.message = message or _DEFAULT_MESSAGES[code]
        super().__init__(self.message)
