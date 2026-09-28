import logging

import pytest

from license_server.domain.errors import ErrorCode, ServiceError
from license_server.http.auth import extract_license_key, require_admin

KEY = "lk_0123456789abcdef0123456789abcdef"
TOKEN = "s3cret-admin-token"


def error_code(call) -> ErrorCode:
    with pytest.raises(ServiceError) as caught:
        call()
    return caught.value.code


class TestExtractLicenseKey:
    @pytest.mark.parametrize("header", [f"Bearer {KEY}", f"bearer {KEY}", f"Bearer   {KEY}  "])
    def test_extracts_key_from_bearer_header(self, header: str) -> None:
        assert extract_license_key(header) == KEY

    @pytest.mark.parametrize(
        "header",
        [None, "", "Bearer", "Bearer ", KEY, f"Basic {KEY}", "Bearer lk_short", f"Bearer {KEY} extra"],
    )
    def test_missing_or_malformed_is_invalid_request(self, header: str | None) -> None:
        assert error_code(lambda: extract_license_key(header)) is ErrorCode.INVALID_REQUEST


class TestRequireAdmin:
    def test_matching_token_passes(self) -> None:
        require_admin(f"Bearer {TOKEN}", TOKEN)

    @pytest.mark.parametrize("header", [None, "", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", TOKEN, f"Basic {TOKEN}"])
    def test_missing_or_wrong_token_is_unauthorized(self, header: str | None) -> None:
        assert error_code(lambda: require_admin(header, TOKEN)) is ErrorCode.UNAUTHORIZED

    @pytest.mark.parametrize("configured", [None, ""])
    def test_unconfigured_token_always_rejects(self, configured: str | None) -> None:
        assert error_code(lambda: require_admin("Bearer ", configured)) is ErrorCode.UNAUTHORIZED
        assert error_code(lambda: require_admin("Bearer anything", configured)) is ErrorCode.UNAUTHORIZED


def test_failures_never_log_credentials(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    for call in [
        lambda: extract_license_key(f"Bearer {KEY}x"),
        lambda: require_admin(f"Bearer {TOKEN}x", TOKEN),
    ]:
        with pytest.raises(ServiceError):
            call()
    assert KEY not in caplog.text and TOKEN not in caplog.text
