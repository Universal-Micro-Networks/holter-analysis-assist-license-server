"""Test-only Cloudflare Access issuer: deterministic RSA key pairs, RS256 JWT signing and a fake certs endpoint.

Pure Python so the tests need no extra packages. Never use these keys outside tests.
"""

import base64
import hashlib
import json
import random
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any

TEAM_DOMAIN = "https://example-team.cloudflareaccess.com"
AUDIENCE = "test-aud-tag"
NOW = 1_790_000_000  # 2026-09-21T12:53:20Z

_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_SMALL_PRIMES = [p for p in range(3, 2000, 2) if all(p % q for q in range(3, int(p**0.5) + 1, 2))]


@dataclass(frozen=True)
class RsaKey:
    kid: str
    n: int
    e: int
    d: int

    def jwk(self) -> dict[str, str]:
        return {"kid": self.kid, "kty": "RSA", "alg": "RS256", "use": "sig", "n": _b64_int(self.n), "e": _b64_int(self.e)}


def _is_probable_prime(candidate: int, rng: random.Random) -> bool:
    if any(candidate % p == 0 for p in _SMALL_PRIMES):
        return candidate in _SMALL_PRIMES
    d, s = candidate - 1, 0
    while d % 2 == 0:
        d, s = d // 2, s + 1
    for _ in range(20):
        x = pow(rng.randrange(2, candidate - 1), d, candidate)
        if x in (1, candidate - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, candidate)
            if x == candidate - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, rng: random.Random) -> int:
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_probable_prime(candidate, rng):
            return candidate


@cache
def rsa_key(kid: str, seed: int, bits: int = 2048) -> RsaKey:
    rng = random.Random(seed)
    e = 65537
    while True:
        p, q = _prime(bits // 2, rng), _prime(bits // 2, rng)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e != 0:
            return RsaKey(kid=kid, n=p * q, e=e, d=pow(e, -1, phi))


def primary_key() -> RsaKey:
    return rsa_key("kid-primary", 1)


def rotated_key() -> RsaKey:
    return rsa_key("kid-rotated", 2)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64_int(value: int) -> str:
    return b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def sign_rs256(key: RsaKey, message: bytes) -> bytes:
    size = (key.n.bit_length() + 7) // 8
    digest_info = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    encoded = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info
    return pow(int.from_bytes(encoded, "big"), key.d, key.n).to_bytes(size, "big")


def claims(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "aud": [AUDIENCE],
        "email": "operator@example.com",
        "exp": NOW + 3600,
        "iat": NOW - 10,
        "nbf": NOW - 10,
        "iss": TEAM_DOMAIN,
        "type": "app",
        "identity_nonce": "nonce",
        "sub": "operator-sub-1",
        "country": "JP",
    }
    base.update(overrides)
    return {name: value for name, value in base.items() if value is not None}


def make_token(key: RsaKey | None = None, header: Mapping[str, Any] | None = None, **claim_overrides: Any) -> str:
    key = key or primary_key()
    head = {"alg": "RS256", "kid": key.kid, "typ": "JWT", **(header or {})}
    signing_input = f"{b64url(json.dumps(head).encode())}.{b64url(json.dumps(claims(**claim_overrides)).encode())}"
    return f"{signing_input}.{b64url(sign_rs256(key, signing_input.encode('ascii')))}"


class FakeCertsFetcher:
    def __init__(self, *keys: RsaKey) -> None:
        self.keys = list(keys) or [primary_key()]
        self.urls: list[str] = []
        self.fail = False

    def fetch(self, url: str) -> Mapping[str, Any]:
        self.urls.append(url)
        if self.fail:
            raise OSError("certs endpoint unreachable")
        return {"keys": [key.jwk() for key in self.keys], "public_cert": {"kid": self.keys[0].kid, "cert": "..."}}
