import json
from collections.abc import Callable

import pytest
from flask.testing import FlaskClient

from license_server.domain.license_key import key_fingerprint
from tests.integration.conftest import ADMIN, ADMIN_TOKEN, bearer


def access_logs(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    lines = capsys.readouterr().out.splitlines()
    return [record for record in map(json.loads, lines) if record.get("event") == "request"]


def test_logs_route_status_fingerprint_and_duration(
    client: FlaskClient, issue: Callable[[int], str], capsys: pytest.CaptureFixture[str]
) -> None:
    key = issue(5)
    capsys.readouterr()
    client.post("/v1/usage", headers=bearer(key))
    [record] = access_logs(capsys)
    assert record["method"] == "POST"
    assert record["route"] == "/v1/usage"
    assert record["status"] == 201
    assert record["error_code"] is None
    assert record["key_fingerprint"] == key_fingerprint(key)
    assert isinstance(record["duration_ms"], (int, float))


def test_logs_error_code(client: FlaskClient, capsys: pytest.CaptureFixture[str]) -> None:
    client.post("/v1/licenses/verify", headers=bearer("lk_" + "0" * 32))
    [record] = access_logs(capsys)
    assert (record["status"], record["error_code"]) == (401, "license_invalid")


def test_admin_requests_log_fingerprint_of_body_key(
    client: FlaskClient, issue: Callable[[int], str], capsys: pytest.CaptureFixture[str]
) -> None:
    key = issue(5)
    capsys.readouterr()
    client.post("/v1/admin/licenses/get", json={"license_key": key}, headers=ADMIN)
    [record] = access_logs(capsys)
    assert record["route"] == "/v1/admin/licenses/get"
    assert record["key_fingerprint"] == key_fingerprint(key)


def test_license_keys_and_credentials_never_appear_in_output(
    client: FlaskClient, issue: Callable[[int], str], capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    key = issue(1)
    client.post("/v1/usage", headers=bearer(key))
    client.post("/v1/usage", headers=bearer(key))
    client.post("/v1/admin/licenses/suspend", json={"license_key": key}, headers=ADMIN)
    client.post("/v1/admin/licenses", json={}, headers=bearer("wrong-" + ADMIN_TOKEN))
    output = capsys.readouterr()
    everything = output.out + output.err + caplog.text
    assert key not in everything
    assert ADMIN_TOKEN not in everything


def test_unmatched_routes_are_logged_without_fingerprint(client: FlaskClient, capsys: pytest.CaptureFixture[str]) -> None:
    client.get("/nope")
    [record] = access_logs(capsys)
    assert (record["route"], record["status"], record["key_fingerprint"]) == (None, 404, None)
