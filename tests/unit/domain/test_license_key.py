import re

import pytest

from license_server.domain.license_key import generate_license_key, is_valid_license_key, key_fingerprint

VALID = "lk_0123456789abcdef0123456789abcdef"


def test_generated_keys_match_the_key_format() -> None:
    for _ in range(100):
        assert re.fullmatch(r"lk_[0-9a-f]{32}", generate_license_key())


def test_generated_keys_are_unique() -> None:
    assert len({generate_license_key() for _ in range(1000)}) == 1000


def test_valid_key_is_accepted() -> None:
    assert is_valid_license_key(VALID)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "lk_",
        VALID[:-1],
        VALID + "0",
        VALID.upper(),
        "lk_0123456789ABCDEF0123456789abcdef",
        "xx_0123456789abcdef0123456789abcdef",
        " " + VALID,
        VALID + "\n",
        "lk_0123456789abcdef0123456789abcdeg",
    ],
)
def test_malformed_keys_are_rejected(value: str) -> None:
    assert not is_valid_license_key(value)


@pytest.mark.parametrize("value", [None, 123, b"lk_0123456789abcdef0123456789abcdef"])
def test_non_string_values_are_rejected(value: object) -> None:
    assert not is_valid_license_key(value)


def test_fingerprint_is_short_stable_and_does_not_contain_the_key() -> None:
    fingerprint = key_fingerprint(VALID)
    assert re.fullmatch(r"[0-9a-f]{8}", fingerprint)
    assert fingerprint == key_fingerprint(VALID)
    assert fingerprint not in VALID
    assert key_fingerprint(generate_license_key()) != fingerprint
