"""Walk through the admin console on the docker compose dev server as the local development operator:
sign in -> issue (unlimited) -> suspend (confirm) -> activate (confirm) -> change memo -> audit trail
-> usage history -> sign out.

Requires CONSOLE_SESSION_SECRET and CONSOLE_DEV_OPERATOR_EMAIL in .dev.vars and no Access settings.
Usage: docker compose up -d app && uv run python scripts/console_smoke.py
"""

import os
import re
import secrets

from console_http import ConsoleBrowser, check

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8787")


def main() -> None:
    browser = ConsoleBrowser(BASE_URL)
    top = browser.get("/console")
    check(top.status == 302 and top.location.endswith("/console/licenses"), "top redirects to the license list")
    listing = browser.get("/console/licenses")
    check(listing.status == 200 and "サインアウト" in listing.body, "dev operator is signed in")
    check("console-local" in browser.cookies, "local session cookie is issued")
    csrf = listing.field("csrf_token")

    memo = f"スモーク確認-{secrets.token_hex(3)}"
    new = browser.get("/console/licenses/new")
    check('name="monthly_limit"' not in new.body, "issue form has no limit field")
    issued = browser.post("/console/licenses", {"memo": memo, "request_id": new.field("request_id")}, csrf)
    check(issued.status == 302 and re.search(r"/console/licenses/lic_[0-9a-f]{16}$", issued.location) is not None,
          "issue redirects to the detail page by public id")
    detail = re.sub(r"^https?://[^/]+", "", issued.location)
    page = browser.get(detail)
    check("ライセンスを発行しました。" in page.body and re.search(r'class="key">lk_[0-9a-f]{32}<', page.body) is not None,
          "detail shows the issued key and success message")
    check("上限" not in page.body and "残り" not in page.body, "detail shows nothing about the limit")

    suspend_id = page.field("request_id", f"{detail}/suspend")
    confirm = browser.post(f"{detail}/suspend", {"request_id": suspend_id}, csrf)
    check(confirm.status == 200 and "このライセンスを停止します" in confirm.body, "suspend asks for confirmation")
    done = browser.post(f"{detail}/suspend", {"request_id": suspend_id, "confirmed": "1"}, csrf)
    page = browser.get(detail)
    check(done.status == 302 and "ライセンスを停止しました。" in page.body and "停止中" in page.body, "suspend after confirmation")

    activate_id = page.field("request_id", f"{detail}/activate")
    done = browser.post(f"{detail}/activate", {"request_id": activate_id, "confirmed": "1"}, csrf)
    page = browser.get(detail)
    check(done.status == 302 and "ライセンスを再開しました。" in page.body, "activate after confirmation")

    memo_done = browser.post(f"{detail}/memo", {"memo": f"{memo}-更新", "request_id": page.field("request_id", f"{detail}/memo")}, csrf)
    page = browser.get(detail)
    check(memo_done.status == 302 and "ライセンシーを変更しました。" in page.body, "memo change")

    audit = page.body.split("操作の記録", 1)[1]
    labels = ["ライセンシーの変更", "再開", "停止", "発行"]
    positions = [audit.find(f"<td>{label}</td>") for label in labels]
    check(all(p >= 0 for p in positions) and positions == sorted(positions), "audit trail lists every change newest first")
    check(f"ライセンシー: 「{memo}」" in audit and f"ライセンシー: 「{memo}-更新」" in audit,
          "audit trail keeps before and after values")

    usage = browser.get(f"{detail}/usage")
    check(usage.status == 200 and "この期間の利用はありません。" in usage.body, "usage history page")

    browser.post("/console/licenses/search", {"q": memo, "status": ""}, csrf)
    found = browser.get("/console/licenses")
    check(f"{memo}-更新" in found.body, "search by memo finds the license")

    signed_out = browser.post("/console/sign-out", {}, csrf)
    check(signed_out.status == 302 and signed_out.location.endswith("/console/signed-out"), "sign out")
    check("console-local" not in browser.cookies, "session cookie is removed")
    print("console smoke passed")


if __name__ == "__main__":
    main()
