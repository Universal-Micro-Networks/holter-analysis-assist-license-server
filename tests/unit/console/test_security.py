import pytest
from flask import Flask

from license_server.console.security import (
    CsrfRejected,
    apply_security_headers,
    is_local_http,
    is_loopback_host,
    verify_csrf,
)

HOST = "https://console.example.com/"
TOKEN = "t" * 43


class TestCsrf:
    def test_matching_token_and_same_origin_pass(self) -> None:
        verify_csrf(TOKEN, TOKEN, "https://console.example.com", None, HOST)

    def test_referer_is_used_when_origin_is_absent(self) -> None:
        verify_csrf(TOKEN, TOKEN, None, "https://console.example.com/console/licenses?page=2", HOST)

    def test_default_port_origin_matches(self) -> None:
        verify_csrf(TOKEN, TOKEN, "http://localhost:8787", None, "http://localhost:8787/")

    @pytest.mark.parametrize("form_token", [None, "", "x" * 43, TOKEN[:-1]])
    def test_missing_or_wrong_token_is_rejected(self, form_token: str | None) -> None:
        with pytest.raises(CsrfRejected):
            verify_csrf(form_token, TOKEN, "https://console.example.com", None, HOST)

    def test_empty_session_token_never_matches(self) -> None:
        with pytest.raises(CsrfRejected):
            verify_csrf("", "", "https://console.example.com", None, HOST)

    @pytest.mark.parametrize(
        ("origin", "referer"),
        [
            ("https://evil.example.com", None),
            ("http://console.example.com", None),
            ("https://console.example.com:8443", None),
            ("null", None),
            (None, "https://evil.example.com/console/licenses"),
            (None, None),
            (None, "not a url"),
            ("https://evil.example.com", "https://console.example.com/console"),
        ],
    )
    def test_foreign_or_missing_source_is_rejected(self, origin: str | None, referer: str | None) -> None:
        with pytest.raises(CsrfRejected):
            verify_csrf(TOKEN, TOKEN, origin, referer, HOST)


class TestHeaders:
    def headers(self, hsts: bool = True) -> dict[str, str]:
        app = Flask(__name__)
        with app.test_request_context():
            response = apply_security_headers(app.make_response("ok"), hsts=hsts)
        return dict(response.headers)

    def test_forbids_framing_scripts_from_elsewhere_caching_and_referrers(self) -> None:
        headers = self.headers()
        csp = headers["Content-Security-Policy"]
        for directive in (
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "base-uri 'none'",
        ):
            assert directive in csp
        assert "unsafe-inline" not in csp
        directives = dict(part.strip().split(" ", 1) for part in csp.split(";"))
        assert directives["img-src"] == "'self' data:"  # Bootstrap's built-in SVG icons
        assert "data:" not in directives["script-src"] and "data:" not in directives["default-src"]
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "same-origin"
        assert headers["Cache-Control"] == "no-store"
        assert headers["Strict-Transport-Security"] == "max-age=31536000"

    def test_referrer_policy_lets_browsers_send_the_origin_on_form_posts(self) -> None:
        # With "no-referrer", browsers send `Origin: null` and no Referer on form posts, so verify_csrf rejects every
        # operator action.
        assert self.headers()["Referrer-Policy"] not in {"no-referrer", ""}

    def test_hsts_can_be_omitted_for_local_http(self) -> None:
        assert "Strict-Transport-Security" not in self.headers(hsts=False)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", True),
        ("localhost:8787", True),
        ("127.0.0.1:8787", True),
        ("[::1]:8787", True),
        ("console.example.com", False),
        ("localhost.example.com", False),
        ("127.0.0.1.nip.io", False),
        ("", False),
    ],
)
def test_loopback_hosts(host: str, expected: bool) -> None:
    assert is_loopback_host(host) is expected


@pytest.mark.parametrize(
    ("scheme", "host", "expected"),
    [
        ("http", "localhost:8787", True),
        ("http", "127.0.0.1:8787", True),
        ("https", "localhost:8787", False),
        ("http", "console.example.com", False),
        ("https", "console.example.com", False),
    ],
)
def test_local_http_is_plain_http_to_loopback_only(scheme: str, host: str, expected: bool) -> None:
    assert is_local_http(scheme, host) is expected
