import json
import re

import pytest

from tests.integration.console.conftest import OPERATOR_EMAIL, Browser, IssueFn


def access_logs(output: str) -> list[dict]:
    return [record for record in map(json.loads, output.splitlines()) if record.get("event") == "request"]


def test_console_access_log_names_operator_and_route_template(
    browser: Browser, issue: IssueFn, capsys: pytest.CaptureFixture[str]
) -> None:
    detail = issue()
    capsys.readouterr()
    browser.get(detail)
    [record] = access_logs(capsys.readouterr().out)
    assert record["route"] == "/console/licenses/<public_id>"
    assert record["operator"] == OPERATOR_EMAIL


def test_api_access_log_has_no_operator(browser: Browser, capsys: pytest.CaptureFixture[str]) -> None:
    browser.client.get("/healthz")
    [record] = access_logs(capsys.readouterr().out)
    assert "operator" not in record


def test_keys_ids_tokens_and_cookies_never_appear_in_output(
    browser: Browser, issue: IssueFn, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    detail = issue(memo="ログ確認")
    page = browser.get(detail)
    key = re.search(r"lk_[0-9a-f]{32}", page.get_data(as_text=True)).group(0)  # type: ignore[union-attr]
    token = browser.token()
    cookie = page.headers.get("Set-Cookie", "").split(";")[0].split("=", 1)[1]
    browser.claims = {"aud": ["wrong"]}
    browser.get(detail)
    output = capsys.readouterr()
    everything = output.out + output.err + caplog.text
    assert key not in everything
    assert detail.rsplit("/", 1)[1] not in everything
    assert token.split(".")[2] not in everything
    assert cookie not in everything
    assert "Access token rejected: bad_audience" in caplog.text
