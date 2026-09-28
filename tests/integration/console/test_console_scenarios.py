"""End-to-end console scenarios with a token signed by the fake Access key."""

import re

from werkzeug.test import TestResponse

from license_server.domain.period import month_period_of
from tests.fakes.sqlite_repository import SqliteRepository
from tests.integration.console.conftest import Browser, IssueFn, detail_path, field_value, issue_via_console

KEY_PATTERN = re.compile(r"lk_[0-9a-f]{32}")


def assert_safe(response: TestResponse) -> None:
    """Every console response: security headers, and no license key in the redirect target or any link."""
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert not KEY_PATTERN.search(response.headers.get("Location", ""))
    html = response.get_data(as_text=True)
    for url in re.findall(r'(?:href|action|src)="([^"]*)"', html):
        assert not KEY_PATTERN.search(url), url
    assert "<script>" not in html and " style=" not in html


def form_request_id(html: str, action: str) -> str:
    form = re.search(rf'<form[^>]*action="{re.escape(action)}".*?</form>', html, flags=re.DOTALL)
    assert form
    return field_value(form.group(0), "request_id")


def test_operator_manages_a_license_from_sign_in_to_sign_out(browser: Browser, repo: SqliteRepository) -> None:
    responses: list[TestResponse] = []

    def get(path: str, **query: str) -> TestResponse:
        response = browser.get(path, **query)
        responses.append(response)
        return response

    def post(path: str, data: dict[str, str]) -> TestResponse:
        response = browser.post(path, data)
        responses.append(response)
        return response

    assert get("/console").status_code == 302
    assert "operator@example.com" in get("/console/licenses").get_data(as_text=True)

    new_page = get("/console/licenses/new").get_data(as_text=True)
    issued = post("/console/licenses", {"memo": "通し確認", "request_id": field_value(new_page, "request_id")})
    detail = detail_path(issued)
    page = get(detail).get_data(as_text=True)
    key = KEY_PATTERN.search(page).group(0)  # type: ignore[union-attr]
    for used_at in ["2026-09-10T00:00:00.000Z", "2026-09-11T00:00:00.000Z"]:
        repo.try_insert_within_limit(key, used_at, month_period_of(2026, 9))

    page = get(detail).get_data(as_text=True)
    suspend_id = form_request_id(page, f"{detail}/suspend")
    post(f"{detail}/suspend", {"request_id": suspend_id})
    post(f"{detail}/suspend", {"request_id": suspend_id, "confirmed": "1"})
    page = get(detail).get_data(as_text=True)
    post(f"{detail}/activate", {"request_id": form_request_id(page, f"{detail}/activate"), "confirmed": "1"})
    page = get(detail).get_data(as_text=True)
    post(f"{detail}/memo", {"memo": "通し確認（更新）", "request_id": form_request_id(page, f"{detail}/memo")})

    page = get(detail).get_data(as_text=True)
    audit = page.split("操作の記録", 1)[1]
    order = [audit.index(f"<td>{label}</td>") for label in ["ライセンシーの変更", "再開", "停止", "発行"]]
    assert order == sorted(order)
    assert "2 回" in page  # usage this month

    usage = get(f"{detail}/usage").get_data(as_text=True)
    assert "2026-09-10 09:00:00" in usage and "2026-09-11 09:00:00" in usage

    signed_out = post("/console/sign-out", {})
    assert signed_out.headers["Location"] == "/cdn-cgi/access/logout"

    for response in responses:
        assert_safe(response)
    license_ = repo.find(key)
    assert license_ is not None
    assert (license_.monthly_limit, license_.status.value, license_.memo) == (0, "active", "通し確認（更新）")
    assert repo.count_audits("issue") == 1


def test_memo_is_escaped_everywhere(browser: Browser, issue: IssueFn) -> None:
    memo = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    detail = issue(memo=memo)
    for path in ["/console/licenses", detail, f"{detail}/usage"]:
        html = browser.get(path).get_data(as_text=True)
        assert "<script>alert" not in html and "<img src=x" not in html
        assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in html
    page = browser.get(detail).get_data(as_text=True)
    browser.post(f"{detail}/memo", {"memo": "安全", "request_id": form_request_id(page, f"{detail}/memo")})
    audit = browser.get(detail).get_data(as_text=True)
    assert "&lt;script&gt;" in audit and "<script>alert" not in audit


def test_storage_outage_during_change_shows_temporary_failure_and_changes_nothing(
    browser: Browser, issue: IssueFn, repo: SqliteRepository
) -> None:
    detail = issue(memo="元")
    page = browser.get(detail).get_data(as_text=True)
    key = KEY_PATTERN.search(page).group(0)  # type: ignore[union-attr]
    request_id = form_request_id(page, f"{detail}/memo")
    csrf = browser.csrf_token()
    repo.unavailable = True
    response = browser.post(f"{detail}/memo", {"memo": "書き換え", "request_id": request_id, "csrf_token": csrf})
    body = response.get_data(as_text=True)
    assert response.status_code == 503
    assert "一時的な障害が発生しました。操作は完了していません。" in body
    assert "simulated" not in body and "Traceback" not in body and "sqlite" not in body.lower()
    assert_safe(response)
    repo.unavailable = False
    assert repo.find(key).memo == "元"  # type: ignore[union-attr]
    assert repo.count_audits("update_memo") == 0


def test_issue_during_outage_creates_nothing(browser: Browser, repo: SqliteRepository) -> None:
    page = browser.get("/console/licenses/new").get_data(as_text=True)
    csrf = browser.csrf_token()
    repo.unavailable = True
    response = browser.post(
        "/console/licenses",
        {"memo": "", "request_id": field_value(page, "request_id"), "csrf_token": csrf},
    )
    assert response.status_code == 503
    repo.unavailable = False
    assert repo.count_licenses() == 0


def test_license_issued_from_the_console_is_unlimited_in_the_api(browser: Browser) -> None:
    issued = issue_via_console(browser, "API確認")
    key = KEY_PATTERN.search(browser.get(detail_path(issued)).get_data(as_text=True)).group(0)  # type: ignore[union-attr]
    client = browser.client
    auth = {"Authorization": f"Bearer {key}"}
    verify = client.post("/v1/licenses/verify", headers=auth)
    assert verify.status_code == 200
    assert verify.get_json()["data"] == {"valid": True, "status": "active", "monthly_limit": 0}
    recorded = [client.post("/v1/usage", headers=auth) for _ in range(3)]
    assert [r.status_code for r in recorded] == [201] * 3
    assert (recorded[-1].get_json()["data"]["used"], recorded[-1].get_json()["data"]["remaining"]) == (3, None)
    assert "Content-Security-Policy" not in recorded[-1].headers
    assert "Set-Cookie" not in recorded[-1].headers
