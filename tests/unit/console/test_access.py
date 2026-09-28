import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from license_server.console.access import AccessDenied, AccessUnavailable, AccessVerifier, WorkersCertsFetcher
from license_server.domain.types import Operator
from tests.fakes.access import (
    AUDIENCE,
    NOW,
    TEAM_DOMAIN,
    FakeCertsFetcher,
    b64url,
    make_token,
    primary_key,
    rotated_key,
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime.fromtimestamp(NOW, UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def fetcher() -> FakeCertsFetcher:
    return FakeCertsFetcher(primary_key())


@pytest.fixture
def verifier(fetcher: FakeCertsFetcher, clock: Clock) -> AccessVerifier:
    return AccessVerifier(TEAM_DOMAIN, AUDIENCE, fetcher, clock)


def denied(verifier: AccessVerifier, token: str) -> str:
    with pytest.raises(AccessDenied) as raised:
        verifier.verify(token)
    return raised.value.reason


def test_valid_token_yields_operator(verifier: AccessVerifier, fetcher: FakeCertsFetcher) -> None:
    assert verifier.verify(make_token()) == Operator(email="operator@example.com", subject="operator-sub-1")
    assert fetcher.urls == [f"{TEAM_DOMAIN}/cdn-cgi/access/certs"]


def test_audience_may_be_a_plain_string(verifier: AccessVerifier) -> None:
    assert verifier.verify(make_token(aud=AUDIENCE)).email == "operator@example.com"


class TestSignature:
    def test_tampered_payload_is_rejected(self, verifier: AccessVerifier) -> None:
        header, _, signature = make_token().split(".")
        forged = b64url(json.dumps({**json.loads(base64.urlsafe_b64decode(make_token().split(".")[1] + "==")), "email": "evil@example.com"}).encode())
        assert denied(verifier, f"{header}.{forged}.{signature}") == "bad_signature"

    def test_signature_from_another_key_with_same_kid_is_rejected(self, verifier: AccessVerifier) -> None:
        other = rotated_key()
        token = make_token(other, header={"kid": primary_key().kid})
        assert denied(verifier, token) == "bad_signature"

    def test_truncated_signature_is_rejected(self, verifier: AccessVerifier) -> None:
        header, payload, signature = make_token().split(".")
        assert denied(verifier, f"{header}.{payload}.{signature[:-8]}") == "bad_signature"

    @pytest.mark.parametrize("alg", ["none", "HS256", "RS512", "ES256", ""])
    def test_algorithms_other_than_rs256_are_rejected(self, verifier: AccessVerifier, alg: str) -> None:
        assert denied(verifier, make_token(header={"alg": alg})) == "bad_algorithm"

    def test_alg_none_without_signature_is_rejected(self, verifier: AccessVerifier) -> None:
        header, payload, _ = make_token(header={"alg": "none"}).split(".")
        assert denied(verifier, f"{header}.{payload}.") == "bad_algorithm"

    def test_hs256_signed_with_public_key_bytes_is_rejected(self, verifier: AccessVerifier) -> None:
        key = primary_key()
        head = b64url(json.dumps({"alg": "HS256", "kid": key.kid}).encode())
        body = make_token().split(".")[1]
        secret = key.n.to_bytes(256, "big")
        mac = hmac.new(secret, f"{head}.{body}".encode(), hashlib.sha256).digest()
        assert denied(verifier, f"{head}.{body}.{b64url(mac)}") == "bad_algorithm"

    @pytest.mark.parametrize("token", ["", "abc", "a.b", "a.b.c.d", "!!.??.**", "e30.e30.e30"])
    def test_malformed_tokens_are_rejected(self, verifier: AccessVerifier, token: str) -> None:
        assert denied(verifier, token) in {"malformed", "bad_algorithm"}


class TestClaims:
    def test_expired_token_is_rejected_after_leeway(self, verifier: AccessVerifier, clock: Clock) -> None:
        token = make_token(exp=NOW - 61)
        assert denied(verifier, token) == "expired"

    def test_expiry_within_leeway_is_accepted(self, verifier: AccessVerifier) -> None:
        assert verifier.verify(make_token(exp=NOW - 30)).subject == "operator-sub-1"

    def test_token_not_yet_valid_is_rejected(self, verifier: AccessVerifier) -> None:
        assert denied(verifier, make_token(nbf=NOW + 120)) == "not_yet_valid"

    def test_nbf_within_leeway_is_accepted(self, verifier: AccessVerifier) -> None:
        assert verifier.verify(make_token(nbf=NOW + 30)).subject == "operator-sub-1"

    @pytest.mark.parametrize("aud", [["other-aud"], "other-aud", [], None])
    def test_other_audience_is_rejected(self, verifier: AccessVerifier, aud: object) -> None:
        assert denied(verifier, make_token(aud=aud)) == "bad_audience"

    @pytest.mark.parametrize("iss", ["https://evil.cloudflareaccess.com", None, f"{TEAM_DOMAIN}/"])
    def test_other_issuer_is_rejected(self, verifier: AccessVerifier, iss: object) -> None:
        assert denied(verifier, make_token(iss=iss)) == "bad_issuer"

    @pytest.mark.parametrize("token_type", ["org", None])
    def test_non_application_token_is_rejected(self, verifier: AccessVerifier, token_type: object) -> None:
        assert denied(verifier, make_token(type=token_type)) == "bad_type"

    @pytest.mark.parametrize("claim", [{"email": ""}, {"email": None}, {"sub": ""}, {"sub": None}, {"email": 1}])
    def test_missing_identity_is_rejected(self, verifier: AccessVerifier, claim: dict) -> None:
        assert denied(verifier, make_token(**claim)) == "missing_identity"

    @pytest.mark.parametrize("exp", [None, "tomorrow", True])
    def test_missing_or_non_numeric_expiry_is_rejected(self, verifier: AccessVerifier, exp: object) -> None:
        assert denied(verifier, make_token(exp=exp)) == "expired"


class TestKeys:
    def test_keys_are_cached_for_an_hour(self, verifier: AccessVerifier, fetcher: FakeCertsFetcher, clock: Clock) -> None:
        verifier.verify(make_token())
        clock.now += timedelta(minutes=59)
        verifier.verify(make_token(exp=NOW + 7200))
        assert len(fetcher.urls) == 1
        clock.now += timedelta(minutes=2)
        verifier.verify(make_token(exp=NOW + 7200))
        assert len(fetcher.urls) == 2

    def test_unknown_kid_refetches_once_and_accepts_rotated_key(self, verifier: AccessVerifier, fetcher: FakeCertsFetcher) -> None:
        verifier.verify(make_token())
        fetcher.keys = [primary_key(), rotated_key()]
        assert verifier.verify(make_token(rotated_key())).email == "operator@example.com"
        assert len(fetcher.urls) == 2

    def test_still_unknown_kid_after_refetch_is_rejected(self, verifier: AccessVerifier, fetcher: FakeCertsFetcher) -> None:
        verifier.verify(make_token())
        assert denied(verifier, make_token(rotated_key())) == "unknown_key"
        assert len(fetcher.urls) == 2

    def test_fetch_failure_is_temporary_not_a_denial(self, verifier: AccessVerifier, fetcher: FakeCertsFetcher) -> None:
        fetcher.fail = True
        with pytest.raises(AccessUnavailable):
            verifier.verify(make_token())

    def test_malformed_certs_response_is_temporary(self, clock: Clock) -> None:
        class BrokenFetcher:
            def fetch(self, url: str) -> dict:
                return {"keys": "not-a-list"}

        with pytest.raises(AccessUnavailable):
            AccessVerifier(TEAM_DOMAIN, AUDIENCE, BrokenFetcher(), clock).verify(make_token())

    def test_non_rsa_keys_in_certs_are_ignored(self, clock: Clock) -> None:
        class MixedFetcher:
            def fetch(self, url: str) -> dict:
                return {"keys": [{"kid": "ec", "kty": "EC", "crv": "P-256", "x": "AA", "y": "AA"}, primary_key().jwk()]}

        assert AccessVerifier(TEAM_DOMAIN, AUDIENCE, MixedFetcher(), clock).verify(make_token()).subject == "operator-sub-1"


class TestWorkersCertsFetcher:
    class Response:
        def __init__(self, status: int, body: str) -> None:
            self.ok = 200 <= status < 300
            self.status = status
            self._body = body

        def text(self) -> str:
            return self._body

    def test_fetches_json_through_the_runtime_fetch(self) -> None:
        calls: list[str] = []

        def fake_fetch(url: str) -> "TestWorkersCertsFetcher.Response":
            calls.append(url)
            return self.Response(200, json.dumps({"keys": []}))

        fetcher = WorkersCertsFetcher(fetch=fake_fetch, run_sync=lambda value: value)
        assert fetcher.fetch(f"{TEAM_DOMAIN}/cdn-cgi/access/certs") == {"keys": []}
        assert calls == [f"{TEAM_DOMAIN}/cdn-cgi/access/certs"]

    def test_error_status_raises(self) -> None:
        fetcher = WorkersCertsFetcher(fetch=lambda url: self.Response(500, "oops"), run_sync=lambda value: value)
        with pytest.raises(OSError):
            fetcher.fetch("https://example.cloudflareaccess.com/cdn-cgi/access/certs")


def test_denial_reason_does_not_include_token_contents(verifier: AccessVerifier) -> None:
    token = make_token(exp=NOW - 3600)
    with pytest.raises(AccessDenied) as raised:
        verifier.verify(token)
    assert token not in str(raised.value)
    assert "operator@example.com" not in str(raised.value)
