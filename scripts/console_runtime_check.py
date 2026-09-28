"""Runtime checks for the admin console on the docker compose dev server:
security headers on every console response, /v1 behavior for console-issued (unlimited) and limited licenses, and double
submissions (resent and concurrent) changing data only once.

Requires CONSOLE_SESSION_SECRET and CONSOLE_DEV_OPERATOR_EMAIL in .dev.vars and no Access settings.
Usage: docker compose up -d app && uv run python scripts/console_runtime_check.py
License keys are read from the detail page only to call /v1 and are never printed.
"""

import http.client
import json
import os
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from console_http import ConsoleBrowser, Page, check

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8787")
KEY_PATTERN = re.compile(r"lk_[0-9a-f]{32}")
CONSOLE_HEADERS = {
    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "cache-control": "no-store",
}


def path_of(location: str) -> str:
    return re.sub(r"^https?://[^/]+", "", location)


def has_console_headers(page: Page) -> bool:
    return all(page.headers.get(name) == value for name, value in CONSOLE_HEADERS.items())


def keeps_keys_out_of_urls(page: Page) -> bool:
    urls = [page.location, *re.findall(r'(?:href|action|src)="([^"]*)"', page.body)]
    return not any(KEY_PATTERN.search(url) for url in urls)


def api(method: str, path: str, key: str | None = None) -> tuple[int, dict[str, str], dict[str, object]]:
    parts = urlsplit(BASE_URL)
    connection = http.client.HTTPConnection(parts.hostname or "localhost", parts.port or 80, timeout=30)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    connection.request(method, path, headers=headers)
    response = connection.getresponse()
    result = response.status, {k.lower(): v for k, v in response.getheaders()}, json.loads(response.read() or b"{}")
    connection.close()
    return result


def issue(browser: ConsoleBrowser, csrf: str, memo: str, limit: int | None = None) -> str:
    """The issue form has no limit field (unlimited); `limit` is posted by hand only to exercise enforcement."""
    new = browser.get("/console/licenses/new")
    form = {"memo": memo, "request_id": new.field("request_id")}
    if limit is not None:
        form["monthly_limit"] = str(limit)
    issued = browser.post("/console/licenses", form, csrf)
    return path_of(issued.location)


def issued_key(browser: ConsoleBrowser, detail: str) -> str:
    match = KEY_PATTERN.search(browser.get(detail).body)
    check(match is not None, "issued key is shown on the detail page")
    return match.group(0) if match else ""


def audit_rows(page: Page, label: str) -> int:
    return page.body.split("操作の記録", 1)[1].count(f"<td>{label}</td>")


def check_security_headers(browser: ConsoleBrowser, csrf: str) -> None:
    detail = issue(browser, csrf, f"ヘッダー確認-{secrets.token_hex(3)}")
    pages = {
        "top redirect": browser.get("/console"),
        "license list": browser.get("/console/licenses"),
        "issue form": browser.get("/console/licenses/new"),
        "license detail": browser.get(detail),
        "usage history": browser.get(f"{detail}/usage"),
        "confirmation": browser.post(f"{detail}/suspend", {"request_id": "confirm-only"}, csrf),
        "input error": browser.post(f"{detail}/memo", {"memo": "改行\nあり", "request_id": "bad-input"}, csrf),
        "unknown license": browser.get("/console/licenses/lic_0000000000000000"),
        "csrf rejection": browser.post(f"{detail}/memo", {"memo": "x", "request_id": "no-csrf"}),
        "foreign origin": browser.post(f"{detail}/memo", {"memo": "x", "request_id": "foreign"}, csrf, "https://evil.example"),
        "signed out page": browser.get("/console/signed-out"),
    }
    for label, page in pages.items():
        check(has_console_headers(page), f"{label} ({page.status}) has the console security headers")
        check(keeps_keys_out_of_urls(page), f"{label} keeps license keys out of URLs and links")
        check("strict-transport-security" not in page.headers, f"{label} skips HSTS on localhost")
    check(pages["csrf rejection"].status == 403 and pages["foreign origin"].status == 403, "forged posts are rejected")
    check(pages["unknown license"].status == 404, "unknown license shows not found")
    after = browser.get(detail)
    check(audit_rows(after, "ライセンシーの変更") == 0 and "停止中" not in after.body, "rejected posts changed nothing")


def check_api(browser: ConsoleBrowser, csrf: str) -> None:
    detail = issue(browser, csrf, f"API確認-{secrets.token_hex(3)}")
    key = issued_key(browser, detail)

    status, headers, body = api("POST", "/v1/licenses/verify", key)
    check(status == 200 and body == {"ok": True, "data": {"valid": True, "status": "active", "monthly_limit": 0}},
          "/v1 verify reports a console-issued license as monthly_limit 0 (unlimited)")
    check("content-security-policy" not in headers and "set-cookie" not in headers, "/v1 gets no console headers or cookie")
    results = [api("POST", "/v1/usage", key) for _ in range(3)]
    data = results[-1][2].get("data")
    check([r[0] for r in results] == [201] * 3 and isinstance(data, dict)
          and (data.get("used"), data.get("monthly_limit"), data.get("remaining")) == (3, 0, None),
          "/v1 usage of an unlimited license is always recorded, with remaining null")

    limited = issued_key(browser, issue(browser, csrf, f"上限確認-{secrets.token_hex(3)}", limit=1))
    status, _, body = api("POST", "/v1/usage", limited)
    data = body.get("data")
    check(status == 201 and isinstance(data, dict) and (data.get("used"), data.get("monthly_limit"), data.get("remaining")) == (1, 1, 0),
          "/v1 usage within a limit is recorded")
    status, _, body = api("POST", "/v1/usage", limited)
    error = body.get("error")
    check(status == 403 and isinstance(error, dict) and error.get("code") == "monthly_limit_reached", "/v1 limit is enforced")
    status, _, body = api("POST", "/v1/usage")
    error = body.get("error")
    check(status == 400 and isinstance(error, dict) and error.get("code") == "invalid_request", "/v1 still requires a key")
    status, _, body = api("POST", "/v1/admin/licenses")
    error = body.get("error")
    check(status == 401 and isinstance(error, dict) and error.get("code") == "unauthorized", "/v1 admin still requires its token")

    usage = browser.get(f"{detail}/usage")
    check(usage.body.count("<tr>") >= 2 and "この期間の利用はありません。" not in usage.body, "console shows the usage recorded through /v1")


def check_double_submission(browser: ConsoleBrowser, csrf: str) -> None:
    memo = f"二重送信確認-{secrets.token_hex(3)}"
    new = browser.get("/console/licenses/new")
    form = {"memo": memo, "request_id": new.field("request_id")}
    first = browser.post("/console/licenses", form, csrf)
    second = browser.post("/console/licenses", form, csrf)
    check(first.location == second.location, "resent issue form leads to the first license")
    check("この操作はすでに実行されています。" in browser.get(path_of(second.location)).body, "resent issue form is reported")
    detail = path_of(first.location)

    page = browser.get(detail)
    memo_id = page.field("request_id", f"{detail}/memo")
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(
            lambda n: browser.post(f"{detail}/memo", {"memo": f"{memo}-{n}", "request_id": memo_id}, csrf), range(5)
        ))
    check(all(r.status == 302 for r in results), "concurrent memo posts all finish without errors")
    page = browser.get(detail)
    check(audit_rows(page, "ライセンシーの変更") == 1, "concurrent memo posts change the memo once")

    browser.post("/console/licenses/search", {"q": memo, "status": ""}, csrf)
    listing = browser.get("/console/licenses")
    check(len(re.findall(r'href="/console/licenses/lic_[0-9a-f]{16}"', listing.body)) == 1, "exactly one license was issued")


def main() -> None:
    browser = ConsoleBrowser(BASE_URL)
    browser.get("/console/licenses")
    csrf = browser.csrf()
    check_security_headers(browser, csrf)
    check_api(browser, csrf)
    check_double_submission(browser, csrf)
    browser.post("/console/sign-out", {}, csrf)
    print("console runtime check passed")


if __name__ == "__main__":
    main()
