"""Verification of the Cloudflare Access application token (`Cf-Access-Jwt-Assertion`).

RS256 is verified in pure Python (no extra packages): the signature is raised to the public exponent and the
result is compared in full against the expected EMSA-PKCS1-v1_5 encoding (RFC 8017 section 8.2.2). Nothing
is parsed out of the decrypted block, which avoids the classic signature-forgery pitfalls.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, TypeGuard

from license_server.domain.types import Operator

logger = logging.getLogger(__name__)

KEY_CACHE_TTL = timedelta(hours=1)
CLOCK_LEEWAY_SECONDS = 60
_MIN_MODULUS_BITS = 2048
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_B64URL = re.compile(r"[A-Za-z0-9_-]*")


class AccessDenied(Exception):
    """The token is missing or invalid. `reason` is a short category safe to log; it never contains token data."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AccessUnavailable(Exception):
    """The Access signing keys could not be obtained; a temporary failure, not a denial."""


class CertsFetcher(Protocol):
    def fetch(self, url: str) -> Mapping[str, Any]: ...


def _pyodide_run_sync(awaitable: Any) -> Any:
    from pyodide.ffi import run_sync

    return run_sync(awaitable)


def _js_fetch(url: str) -> Any:
    from js import fetch  # type: ignore[import-not-found]  # only exists inside the Workers runtime

    return fetch(url)


class WorkersCertsFetcher:
    """Fetches the Access JWKS with the Workers runtime `fetch`, made synchronous for the WSGI request."""

    def __init__(self, fetch: Callable[[str], Any] = _js_fetch, run_sync: Callable[[Any], Any] = _pyodide_run_sync) -> None:
        self._fetch = fetch
        self._run_sync = run_sync

    def fetch(self, url: str) -> Mapping[str, Any]:
        response = self._run_sync(self._fetch(url))
        if not response.ok:
            raise OSError(f"certs endpoint answered {response.status}")
        document: Mapping[str, Any] = json.loads(str(self._run_sync(response.text())))
        return document


@dataclass(frozen=True)
class _PublicKey:
    n: int
    e: int


class AccessVerifier:
    def __init__(
        self, team_domain: str, audience: str, fetcher: CertsFetcher, clock: Callable[[], datetime]
    ) -> None:
        self._issuer = team_domain
        self._audience = audience
        self._certs_url = f"{team_domain}/cdn-cgi/access/certs"
        self._fetcher = fetcher
        self._clock = clock
        self._keys: dict[str, _PublicKey] | None = None
        self._fetched_at: datetime | None = None

    def verify(self, token: str) -> Operator:
        try:
            return self._verify(token)
        except AccessDenied as denial:
            logger.warning("Access token rejected: %s", denial.reason)
            raise

    def _verify(self, token: str) -> Operator:
        parts = token.split(".")
        if len(parts) != 3:
            raise AccessDenied("malformed")
        header_b64, payload_b64, signature_b64 = parts
        header = _json_segment(header_b64)
        if header.get("alg") != "RS256":
            raise AccessDenied("bad_algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AccessDenied("unknown_key")
        key = self._key(kid)
        if not _rs256_valid(key, f"{header_b64}.{payload_b64}".encode("ascii"), _decode(signature_b64)):
            raise AccessDenied("bad_signature")
        return self._operator(_json_segment(payload_b64))

    def _operator(self, claims: Mapping[str, Any]) -> Operator:
        now = self._clock().timestamp()
        if claims.get("iss") != self._issuer:
            raise AccessDenied("bad_issuer")
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if self._audience not in audiences:
            raise AccessDenied("bad_audience")
        expires = claims.get("exp")
        if not _is_number(expires) or expires + CLOCK_LEEWAY_SECONDS <= now:
            raise AccessDenied("expired")
        not_before = claims.get("nbf")
        if not_before is not None and (not _is_number(not_before) or not_before - CLOCK_LEEWAY_SECONDS > now):
            raise AccessDenied("not_yet_valid")
        if claims.get("type") != "app":
            raise AccessDenied("bad_type")
        email, subject = claims.get("email"), claims.get("sub")
        if not isinstance(email, str) or not email or not isinstance(subject, str) or not subject:
            raise AccessDenied("missing_identity")
        return Operator(email=email, subject=subject)

    def _key(self, kid: str) -> _PublicKey:
        now = self._clock()
        refreshed = False
        if self._keys is None or self._fetched_at is None or now - self._fetched_at >= KEY_CACHE_TTL:
            self._refresh(now)
            refreshed = True
        assert self._keys is not None
        key = self._keys.get(kid)
        if key is None and not refreshed:
            # Access rotates its signing keys; an unknown kid may simply be newer than the cache.
            self._refresh(now)
            key = self._keys.get(kid)
        if key is None:
            raise AccessDenied("unknown_key")
        return key

    def _refresh(self, now: datetime) -> None:
        try:
            document = self._fetcher.fetch(self._certs_url)
            entries = document["keys"]
            if not isinstance(entries, list):
                raise TypeError("keys is not a list")
            keys = {entry["kid"]: key for entry in entries if (key := _rsa_key(entry)) is not None}
        except Exception as error:
            logger.error("Access certs unavailable: %s", type(error).__name__)
            raise AccessUnavailable("could not load Access signing keys") from error
        self._keys, self._fetched_at = keys, now


def _rsa_key(entry: Any) -> _PublicKey | None:
    if not isinstance(entry, Mapping) or entry.get("kty") != "RSA" or not isinstance(entry.get("kid"), str):
        return None
    try:
        key = _PublicKey(n=int.from_bytes(_decode(entry["n"]), "big"), e=int.from_bytes(_decode(entry["e"]), "big"))
    except (AccessDenied, KeyError, TypeError):
        return None
    return key if key.n.bit_length() >= _MIN_MODULUS_BITS and key.e > 1 else None


def _rs256_valid(key: _PublicKey, message: bytes, signature: bytes) -> bool:
    size = (key.n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    value = int.from_bytes(signature, "big")
    if value >= key.n:
        return False
    recovered = pow(value, key.e, key.n).to_bytes(size, "big")
    digest_info = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    expected = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info
    return hmac.compare_digest(recovered, expected)


def _decode(segment: Any) -> bytes:
    if not isinstance(segment, str) or not _B64URL.fullmatch(segment):
        raise AccessDenied("malformed")
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError) as error:
        raise AccessDenied("malformed") from error


def _json_segment(segment: str) -> dict[str, Any]:
    try:
        value = json.loads(_decode(segment))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AccessDenied("malformed") from error
    if not isinstance(value, dict):
        raise AccessDenied("malformed")
    return value


def _is_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)
