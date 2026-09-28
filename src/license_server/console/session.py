"""Per-operator console session in a signed (not encrypted) cookie.

Flask's built-in session is not used: its secret must be set when the app is created, but on Workers the
secret is only readable inside a request. Only the operator's Access subject, timestamps, the CSRF token,
flash messages and the operator's own search input are stored; nothing else about licenses.
"""

import secrets
from datetime import datetime, timedelta
from typing import Any

from flask import Response
from itsdangerous import BadSignature, URLSafeSerializer

from license_server.domain.types import LicenseStatus, Operator

COOKIE_NAME = "__Host-console"
# Safari does not store Secure cookies over http://localhost, and a __Host- cookie must be Secure.
LOCAL_COOKIE_NAME = "console-local"
IDLE_TIMEOUT = timedelta(hours=8)
_SALT = "license-server.console-session"


def cookie_name(secure: bool) -> str:
    return COOKIE_NAME if secure else LOCAL_COOKIE_NAME


class SessionExpired(Exception):
    """No activity for longer than IDLE_TIMEOUT; the operator must sign in again."""


class ConsoleSession:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._cleared = False

    @classmethod
    def load(cls, cookie: str | None, secret: str) -> "ConsoleSession":
        if not cookie:
            return cls({})
        try:
            data = URLSafeSerializer(secret, salt=_SALT).loads(cookie)
        except BadSignature:
            return cls({})
        return cls(data if isinstance(data, dict) else {})

    def dump(self, secret: str) -> str:
        return URLSafeSerializer(secret, salt=_SALT).dumps(self._data)

    def save(self, response: Response, secret: str, secure: bool = True) -> None:
        name = cookie_name(secure)
        if self._cleared:
            response.delete_cookie(name, path="/", secure=secure, httponly=True, samesite="Lax")
            return
        response.set_cookie(name, self.dump(secret), path="/", secure=secure, httponly=True, samesite="Lax")

    def touch(self, operator: Operator, now: datetime) -> bool:
        """Start a session for a new operator (returns True) or extend the current one; raises SessionExpired."""
        epoch = int(now.timestamp())
        if self._data.get("operator_subject") != operator.subject:
            self._data = {"operator_subject": operator.subject, "csrf_token": secrets.token_urlsafe(32), "last_seen": epoch}
            self._cleared = False
            return True
        last_seen = self._data.get("last_seen")
        if not isinstance(last_seen, int) or epoch - last_seen > IDLE_TIMEOUT.total_seconds():
            raise SessionExpired()
        self._data["last_seen"] = epoch
        return False

    @property
    def csrf_token(self) -> str:
        token = self._data.get("csrf_token")
        return token if isinstance(token, str) else ""

    @property
    def is_cleared(self) -> bool:
        return self._cleared

    def clear(self) -> None:
        self._data = {}
        self._cleared = True

    def flash(self, message: str) -> None:
        self._data.setdefault("flashes", []).append(message)

    def pop_flashes(self) -> list[str]:
        flashes = self._data.pop("flashes", [])
        return [message for message in flashes if isinstance(message, str)] if isinstance(flashes, list) else []

    def set_search(self, query: str | None, status: LicenseStatus | None) -> None:
        self._data["search"] = {"q": query or "", "status": status.value if status else ""}

    def search(self) -> tuple[str | None, LicenseStatus | None]:
        stored = self._data.get("search")
        if not isinstance(stored, dict):
            return None, None
        query, status = stored.get("q"), stored.get("status")
        valid_status = isinstance(status, str) and status in {member.value for member in LicenseStatus}
        return (query if isinstance(query, str) and query else None), (LicenseStatus(str(status)) if valid_status else None)
