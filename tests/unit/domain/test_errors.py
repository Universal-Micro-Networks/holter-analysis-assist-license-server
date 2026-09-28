import pytest

from license_server.domain.errors import ErrorCode, ServiceError

DESIGNED_CODES = {
    "invalid_request",
    "license_invalid",
    "license_suspended",
    "monthly_limit_reached",
    "unauthorized",
    "license_not_found",
    "rate_limited",
    "temporary_failure",
}


def test_error_codes_match_design_and_are_distinct() -> None:
    values = [code.value for code in ErrorCode]
    assert set(values) == DESIGNED_CODES
    assert len(values) == len(set(values))


def test_error_code_serializes_as_plain_string() -> None:
    assert ErrorCode.MONTHLY_LIMIT_REACHED == "monthly_limit_reached"
    assert str(ErrorCode.MONTHLY_LIMIT_REACHED) == "monthly_limit_reached"


@pytest.mark.parametrize("code", list(ErrorCode))
def test_service_error_uses_fixed_default_message(code: ErrorCode) -> None:
    error = ServiceError(code)
    assert error.code is code
    assert error.message
    assert str(error) == error.message


def test_default_messages_are_distinct_per_code() -> None:
    messages = [ServiceError(code).message for code in ErrorCode]
    assert len(messages) == len(set(messages))


def test_service_error_accepts_explicit_message() -> None:
    error = ServiceError(ErrorCode.INVALID_REQUEST, "monthly_limit must be an integer")
    assert error.message == "monthly_limit must be an integer"


def test_service_error_can_be_caught_as_exception() -> None:
    with pytest.raises(ServiceError) as caught:
        raise ServiceError(ErrorCode.LICENSE_SUSPENDED)
    assert caught.value.code is ErrorCode.LICENSE_SUSPENDED
