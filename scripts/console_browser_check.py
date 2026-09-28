"""Operate the admin console in real browsers (Chrome and WebKit, the engine behind Safari) against the
docker compose dev server: open the menu -> issue -> suspend with confirmation -> search with Enter ->
filter by choosing a status -> sign out from the menu.

Unlike console_smoke.py, the browser decides which cookies to keep and which Origin/Referer to send, so this
catches policies that make browsers drop the session or send `Origin: null`.

Usage: docker compose up -d app && uv run --with playwright python scripts/console_browser_check.py [chrome] [webkit]
Chrome must be installed; WebKit needs `uv run --with playwright playwright install webkit` once.
License keys are never printed.
"""

import re
import secrets
import sys
from typing import Any

from playwright.sync_api import Page, sync_playwright

from console_http import check

BASE_URL = "http://localhost:8787"


def open_menu(page: Page) -> None:
    page.click("button[aria-controls=console-menu]")
    page.locator("#console-menu.show").wait_for()


def listed(page: Page) -> list[str]:
    return page.locator("tbody td:first-child").all_inner_texts()


def walk(page: Page, name: str) -> None:
    posts: list[int] = []
    errors: list[str] = []
    page.on("response", lambda r: posts.append(r.status) if r.request.method == "POST" else None)
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    page.goto(f"{BASE_URL}/console/licenses")
    check(bool(page.context.cookies()), f"[{name}] browser keeps the session cookie")

    memo = f"ブラウザ確認-{secrets.token_hex(3)}"
    headings = page.locator("h1:not(.visually-hidden):visible, h2:not(.visually-hidden):visible")
    check(headings.count() == 0, f"[{name}] no visible page headings")
    check(not page.get_by_text("新しいライセンスを発行する").is_visible(), f"[{name}] issue link is hidden until the menu opens")
    open_menu(page)
    page.click("text=新しいライセンスを発行する")
    check(page.locator("input[name=monthly_limit]").count() == 0, f"[{name}] issue form has no limit field")
    page.fill("input[name=memo]", memo)
    page.click("button:has-text('発行する')")
    body = page.content()
    check("ライセンスを発行しました。" in body and re.search(r"lk_[0-9a-f]{32}", body) is not None, f"[{name}] issue")
    check(re.search(r"lk_[0-9a-f]{32}", page.url) is None, f"[{name}] detail URL has no license key")
    check("上限" not in page.inner_text("main"), f"[{name}] detail shows nothing about the limit")

    page.locator("form[action$='/suspend'] button[type=submit]").click()
    check("このライセンスを停止します" in page.content(), f"[{name}] suspend asks for confirmation")
    page.locator("form[action$='/suspend'] button[type=submit]").click()
    check("ライセンスを停止しました。" in page.content(), f"[{name}] suspend after confirmation")

    page.goto(f"{BASE_URL}/console/licenses")
    search = page.locator("form[action='/console/licenses/search']")
    check(search.locator("button").count() == 0, f"[{name}] search form has no button")
    page.fill("input[name=q]", memo)
    with page.expect_navigation():
        page.press("input[name=q]", "Enter")
    check(listed(page) == [memo] and page.input_value("input[name=q]") == memo, f"[{name}] Enter searches")
    with page.expect_navigation():
        page.select_option("select[name=status]", "active")
    check(listed(page) == [] and page.input_value("select[name=status]") == "active",
          f"[{name}] choosing a status searches (the suspended license is filtered out)")
    with page.expect_navigation():
        page.select_option("select[name=status]", "suspended")
    check(listed(page) == [memo], f"[{name}] choosing another status searches again")

    open_menu(page)
    page.click("text=サインアウト")
    check(page.url.endswith("/console/signed-out"), f"[{name}] sign out")
    check(all(status < 400 for status in posts), f"[{name}] no form post was rejected")
    for error in errors:
        print(f"     browser console: {re.sub(r'lk_[0-9a-f]{32}', 'lk_<redacted>', error)}")
    check(not errors, f"[{name}] no browser console errors (such as CSP violations)")


def main() -> None:
    targets = sys.argv[1:] or ["chrome", "webkit"]
    with sync_playwright() as playwright:
        launchers: dict[str, tuple[Any, dict[str, Any]]] = {
            "chrome": (playwright.chromium, {"channel": "chrome"}),
            "webkit": (playwright.webkit, {}),
        }
        for name in targets:
            browser_type, options = launchers[name]
            browser = browser_type.launch(**options)
            walk(browser.new_page(), name)
            browser.close()
    print("console browser check passed")


if __name__ == "__main__":
    main()
