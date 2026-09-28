import hashlib
import re
import secrets

_PREFIX = "lk_"
_PATTERN = re.compile(r"lk_[0-9a-f]{32}")


def generate_license_key() -> str:
    return _PREFIX + secrets.token_hex(16)


def is_valid_license_key(value: object) -> bool:
    return isinstance(value, str) and _PATTERN.fullmatch(value) is not None


def key_fingerprint(license_key: str) -> str:
    """Short, non-reversible identifier for logs; the key itself must never be logged."""
    return hashlib.sha256(license_key.encode("utf-8")).hexdigest()[:8]
