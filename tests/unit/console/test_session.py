from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask

from license_server.console.session import (
    COOKIE_NAME,
    IDLE_TIMEOUT,
    LOCAL_COOKIE_NAME,
    ConsoleSession,
    SessionExpired,
    cookie_name,
)
from license_server.domain.types import LicenseStatus, Operator

SECRET = "test-session-secret-0123456789abcdef"
ALICE = Operator("alice@example.com", "sub-alice")
BOB = Operator("bob@example.com", "sub-bob")
T0 = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)


def roundtrip(session: ConsoleSession, secret: str = SECRET) -> ConsoleSession:
    return ConsoleSession.load(session.dump(secret), secret)


def test_first_touch_starts_a_new_session_with_csrf_token() -> None:
    session = ConsoleSession.load(None, SECRET)
    assert session.touch(ALICE, T0) is True
    assert len(session.csrf_token) >= 32


def test_touch_within_idle_timeout_keeps_session_and_token() -> None:
    session = ConsoleSession.load(None, SECRET)
    session.touch(ALICE, T0)
    token = session.csrf_token
    restored = roundtrip(session)
    assert restored.touch(ALICE, T0 + timedelta(hours=1)) is False
    assert restored.csrf_token == token


def test_exactly_eight_hours_idle_is_still_valid() -> None:
    session = ConsoleSession.load(None, SECRET)
    session.touch(ALICE, T0)
    assert roundtrip(session).touch(ALICE, T0 + IDLE_TIMEOUT) is False


def test_more_than_eight_hours_idle_expires() -> None:
    session = ConsoleSession.load(None, SECRET)
    session.touch(ALICE, T0)
    with pytest.raises(SessionExpired):
        roundtrip(session).touch(ALICE, T0 + IDLE_TIMEOUT + timedelta(seconds=1))


def test_activity_slides_the_idle_window() -> None:
    session = ConsoleSession.load(None, SECRET)
    session.touch(ALICE, T0)
    session = roundtrip(session)
    session.touch(ALICE, T0 + timedelta(hours=7))
    assert roundtrip(session).touch(ALICE, T0 + timedelta(hours=14)) is False


def test_different_operator_gets_a_fresh_session_and_token() -> None:
    session = ConsoleSession.load(None, SECRET)
    session.touch(ALICE, T0)
    session.flash("alice only")
    session.set_search("山田", LicenseStatus.ACTIVE)
    token = session.csrf_token
    restored = roundtrip(session)
    assert restored.touch(BOB, T0 + timedelta(minutes=1)) is True
    assert restored.csrf_token != token
    assert restored.pop_flashes() == []
    assert restored.search() == (None, None)


def test_tampered_or_foreign_cookie_is_ignored() -> None:
    session = ConsoleSession.load(None, SECRET)
    session.touch(ALICE, T0)
    cookie = session.dump(SECRET)
    assert ConsoleSession.load(cookie[:-2] + "xx", SECRET).touch(ALICE, T0) is True
    assert ConsoleSession.load(cookie, "another-secret-0123456789abcdef").touch(ALICE, T0) is True
    assert ConsoleSession.load("garbage", SECRET).touch(ALICE, T0) is True


def test_flashes_are_shown_once() -> None:
    session = ConsoleSession.load(None, SECRET)
    session.touch(ALICE, T0)
    session.flash("発行しました")
    restored = roundtrip(session)
    assert restored.pop_flashes() == ["発行しました"]
    assert roundtrip(restored).pop_flashes() == []


def test_search_criteria_are_kept() -> None:
    session = ConsoleSession.load(None, SECRET)
    session.touch(ALICE, T0)
    session.set_search("lk_abc", LicenseStatus.SUSPENDED)
    assert roundtrip(session).search() == ("lk_abc", LicenseStatus.SUSPENDED)
    session.set_search(None, None)
    assert roundtrip(session).search() == (None, None)


def test_clear_removes_everything() -> None:
    session = ConsoleSession.load(None, SECRET)
    session.touch(ALICE, T0)
    session.clear()
    assert session.is_cleared
    assert ConsoleSession.load(session.dump(SECRET), SECRET).touch(ALICE, T0) is True


def test_cookie_holds_no_operator_email_in_plain_payload_beyond_subject() -> None:
    session = ConsoleSession.load(None, SECRET)
    session.touch(ALICE, T0)
    assert "alice@example.com" not in session.dump(SECRET)


class TestCookie:
    def test_saved_with_host_prefix_and_strict_attributes(self) -> None:
        app = Flask(__name__)
        session = ConsoleSession.load(None, SECRET)
        session.touch(ALICE, T0)
        with app.test_request_context():
            response = app.make_response("ok")
            session.save(response, SECRET)
        header = response.headers["Set-Cookie"]
        assert header.startswith(f"{COOKIE_NAME}=")
        for attribute in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/"):
            assert attribute in header
        assert "Domain=" not in header
        assert "Expires=" not in header and "Max-Age=" not in header

    def test_cleared_session_deletes_cookie(self) -> None:
        app = Flask(__name__)
        session = ConsoleSession.load(None, SECRET)
        session.touch(ALICE, T0)
        session.clear()
        with app.test_request_context():
            response = app.make_response("ok")
            session.save(response, SECRET)
        header = response.headers["Set-Cookie"]
        assert header.startswith(f"{COOKIE_NAME}=;")
        assert "Max-Age=0" in header or "Expires=Thu, 01 Jan 1970" in header

    def test_local_http_cookie_drops_secure_and_host_prefix(self) -> None:
        # Safari does not store Secure cookies over http://localhost, and __Host- cookies must be Secure.
        app = Flask(__name__)
        session = ConsoleSession.load(None, SECRET)
        session.touch(ALICE, T0)
        with app.test_request_context():
            response = app.make_response("ok")
            session.save(response, SECRET, secure=False)
        header = response.headers["Set-Cookie"]
        assert header.startswith(f"{LOCAL_COOKIE_NAME}=")
        assert "Secure" not in header
        for attribute in ("HttpOnly", "SameSite=Lax", "Path=/"):
            assert attribute in header

    def test_local_http_clear_deletes_the_local_cookie(self) -> None:
        app = Flask(__name__)
        session = ConsoleSession.load(None, SECRET)
        session.clear()
        with app.test_request_context():
            response = app.make_response("ok")
            session.save(response, SECRET, secure=False)
        assert response.headers["Set-Cookie"].startswith(f"{LOCAL_COOKIE_NAME}=;")

    def test_cookie_name_depends_on_secure(self) -> None:
        assert (cookie_name(secure=True), cookie_name(secure=False)) == (COOKIE_NAME, LOCAL_COOKIE_NAME)
