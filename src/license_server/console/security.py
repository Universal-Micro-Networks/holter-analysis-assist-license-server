import hmac
from urllib.parse import urlsplit

from flask import Response

CONTENT_SECURITY_POLICY = (
    # data: images are Bootstrap's built-in SVG icons (menu, select arrows, close buttons); they cannot run scripts.
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class CsrfRejected(Exception):
    """A state-changing request without a valid token or from another site; nothing was executed."""


def verify_csrf(
    form_token: str | None, session_token: str, origin: str | None, referer: str | None, host_url: str
) -> None:
    if not session_token or not form_token or not hmac.compare_digest(form_token, session_token):
        raise CsrfRejected("csrf token mismatch")
    expected = _origin_of(host_url)
    source = _origin_of(origin) if origin else _origin_of(referer)
    if source is None or source != expected:
        raise CsrfRejected("foreign origin")


def _origin_of(url: str | None) -> str | None:
    if not url or url == "null":
        return None
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc.lower()}"


def apply_security_headers(response: Response, hsts: bool) -> Response:
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # "no-referrer" would make browsers send `Origin: null` on form posts, which verify_csrf rejects.
    response.headers["Referrer-Policy"] = "same-origin"
    # Detail pages show the full license key; keep them out of every cache.
    response.headers["Cache-Control"] = "no-store"
    if hsts:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def is_local_http(scheme: str, host: str) -> bool:
    """Plain http to this machine: the only case where the session cookie may go without Secure."""
    return scheme == "http" and is_loopback_host(host)


def is_loopback_host(host: str) -> bool:
    name = host.strip().lower()
    if name.startswith("["):
        name = name[1:].split("]", 1)[0]
    elif name.count(":") == 1:
        name = name.split(":", 1)[0]
    return name in _LOOPBACK_HOSTS
