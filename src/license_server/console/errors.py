"""HTML error pages and response hooks for /console; the rest of the app keeps its JSON behavior."""

import logging

from flask import Flask, Response, g, render_template, request
from werkzeug.exceptions import HTTPException

from license_server.console import messages
from license_server.console.access import AccessUnavailable
from license_server.console.routes import NotConfigured, NotSignedIn
from license_server.console.security import CsrfRejected, apply_security_headers, is_local_http, is_loopback_host
from license_server.console.session import ConsoleSession
from license_server.domain.errors import ErrorCode, ServiceError
from license_server.http.responses import status_for
from license_server.repository.base import RepositoryUnavailable

logger = logging.getLogger(__name__)

_TEMPORARY = (503, messages.error_message(ErrorCode.TEMPORARY_FAILURE))


def is_console_request() -> bool:
    return request.path == "/console" or request.path.startswith("/console/")


def console_error_response(error: Exception) -> Response:
    status, message = _classify(error)
    signed_in = status != 403 or isinstance(error, CsrfRejected)
    session: ConsoleSession | None = g.get("console_session")
    html = render_template(
        "console/error.html",
        status=status,
        message=message,
        operator=g.get("console_operator") if signed_in else None,
        csrf_token=session.csrf_token if session else "",
        flashes=[],
    )
    return Response(html, status=status, mimetype="text/html")


def _classify(error: Exception) -> tuple[int, str]:
    if isinstance(error, NotSignedIn):
        return 403, messages.NOT_SIGNED_IN
    if isinstance(error, CsrfRejected):
        logger.warning("console request rejected: %s", error)
        return 403, messages.CSRF_REJECTED
    if isinstance(error, NotConfigured):
        return 503, messages.NOT_CONFIGURED
    if isinstance(error, ServiceError):
        return status_for(error.code), messages.error_message(error.code)
    if isinstance(error, HTTPException):
        status = error.code or 400
        return status, messages.PAGE_NOT_FOUND if status == 404 else messages.error_message(ErrorCode.INVALID_REQUEST)
    if isinstance(error, RepositoryUnavailable | AccessUnavailable):
        logger.error("console temporary failure: %s", type(error).__name__)
        return _TEMPORARY
    logger.exception("unexpected console error")
    return _TEMPORARY


def install_console_response_hooks(app: Flask) -> None:
    @app.after_request
    def finish_console_response(response: Response) -> Response:
        if not is_console_request():
            return response
        session: ConsoleSession | None = g.get("console_session")
        secret: str | None = g.get("console_session_secret")
        # A failed request must not persist a new session, or its sign-in would never be recorded.
        if session is not None and secret is not None and response.status_code < 500:
            session.save(response, secret, secure=not is_local_http(request.scheme, request.host))
        return apply_security_headers(response, hsts=not is_loopback_host(request.host))
