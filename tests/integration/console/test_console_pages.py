import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from license_server.domain.period import month_period_of
from license_server.domain.types import AuditContext, Operator
from tests.conftest import FixedClock
from tests.fakes.sqlite_repository import SqliteRepository
from tests.integration.console.conftest import Browser, IssueFn, detail_path, field_value, issue_via_console


def form_field(html: str, action: str, name: str) -> str:
    """Value of `name` inside the form posting to `action` (pages have several forms)."""
    match = re.search(rf'<form[^>]*action="{re.escape(action)}".*?</form>', html, flags=re.DOTALL)
    assert match, f"no form for {action}"
    return field_value(match.group(0), name)


def key_on(browser: Browser, path: str) -> str:
    match = re.search(r'id="license-key" class="key">(lk_[0-9a-f]{32})<', browser.get(path).get_data(as_text=True))
    assert match
    return match.group(1)


def post_form(browser: Browser, detail: str, suffix: str, data: dict[str, str] | None = None, confirmed: bool = False):  # type: ignore[no-untyped-def]
    action = f"{detail}/{suffix}"
    request_id = form_field(browser.get(detail).get_data(as_text=True), action, "request_id")
    form = {"request_id": request_id, **(data or {})}
    if confirmed:
        form["confirmed"] = "1"
    return browser.post(action, form)


def post_limit(browser: Browser, detail: str, monthly_limit: str):  # type: ignore[no-untyped-def]
    """The limit route has no form on the page, so the request id is made here."""
    return browser.post(f"{detail}/limit", {"monthly_limit": monthly_limit, "request_id": str(uuid.uuid4())})


def visible_headings(html: str) -> list[str]:
    return [m.group(0) for m in re.finditer(r"<h[1-6](?![^>]*visually-hidden)[^>]*>.*?</h[1-6]>", html, flags=re.DOTALL)]


def outside_menu(html: str) -> str:
    return re.sub(r'<div class="offcanvas[^"]*"[^>]*id="console-menu".*?</nav>', "", html, flags=re.DOTALL)


class TestLayout:
    def test_pages_have_no_visible_page_headings(self, browser: Browser, issue: IssueFn) -> None:
        detail = issue(memo="見出し確認")
        for path in ["/console/licenses", "/console/licenses/new", detail, f"{detail}/usage"]:
            html = browser.get(path).get_data(as_text=True)
            assert visible_headings(html) == [], path
            assert re.search(r'<h1 class="visually-hidden">', html), path

    def test_issue_and_sign_out_are_only_in_the_hamburger_menu(self, browser: Browser) -> None:
        html = browser.get("/console/licenses").get_data(as_text=True)
        assert 'data-bs-toggle="offcanvas"' in html and 'data-bs-target="#console-menu"' in html
        menu = html[html.index('id="console-menu"'):]
        assert 'href="/console/licenses/new"' in menu and 'action="/console/sign-out"' in menu
        rest = outside_menu(html)
        assert 'href="/console/licenses/new"' not in rest and 'action="/console/sign-out"' not in rest

    def test_memo_is_called_licensee_everywhere(self, browser: Browser, issue: IssueFn) -> None:
        detail = issue(memo="呼び名確認")
        page = browser.get(detail).get_data(as_text=True)
        browser.post(f"{detail}/memo", {"memo": "呼び名確認2", "request_id": form_field(page, f"{detail}/memo", "request_id")})
        for path in ["/console/licenses", "/console/licenses/new", detail, f"{detail}/usage"]:
            html = browser.get(path).get_data(as_text=True)
            assert "メモ" not in html, path
            if not path.endswith("/usage"):
                assert "ライセンシー" in html, path

    def test_licensee_placeholder_when_empty(self, browser: Browser, issue: IssueFn) -> None:
        issue(memo="")
        assert "（ライセンシー未設定）" in browser.get("/console/licenses").get_data(as_text=True)

    def test_monthly_limit_is_not_shown_anywhere(self, browser: Browser, issue: IssueFn) -> None:
        detail = issue(memo="表示確認")
        for path in ["/console/licenses", "/console/licenses/new", detail, f"{detail}/usage"]:
            html = browser.get(path).get_data(as_text=True)
            assert "上限" not in html and "残り" not in html, path
            assert 'name="monthly_limit"' not in html and f'action="{detail}/limit"' not in html, path
            assert "sort=monthly_limit" not in html, path

    def test_framework_assets_are_served_from_this_site(self, browser: Browser) -> None:
        html = browser.get("/console/licenses").get_data(as_text=True)
        assert '<link rel="stylesheet" href="/console/assets/vendor/bootstrap/bootstrap.min.css">' in html
        assert '<script src="/console/assets/vendor/bootstrap/bootstrap.bundle.min.js" defer></script>' in html
        assert not re.search(r'(?:href|src)="(?:https?:)?//', html)


class TestList:
    def test_shows_licensee_status_usage_and_issue_time(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue(memo="山田病院")
        repo.try_insert_within_limit(key_on(browser, detail), "2026-09-10T00:00:00.000Z", month_period_of(2026, 9))
        html = browser.get("/console/licenses").get_data(as_text=True)
        assert "山田病院" in html and "有効" in html and ">1<" in html
        assert "2026-09-15 12:00:00" in html  # clock is 03:00 UTC

    def test_search_form_submits_without_a_button(self, browser: Browser) -> None:
        html = browser.get("/console/licenses").get_data(as_text=True)
        form = re.search(r'<form[^>]*action="/console/licenses/search"[^>]*>.*?</form>', html, flags=re.DOTALL)
        assert form and "data-auto-submit" in form.group(0) and 'role="search"' in form.group(0)
        assert "<button" not in form.group(0)
        # Enter submits natively only while the form has a single text field.
        assert len(re.findall(r'<input type="(?:text|search)"', form.group(0))) == 1
        assert '<select class="form-select" id="status" name="status">' in form.group(0)

    def test_newest_first(self, browser: Browser, issue: IssueFn, clock: FixedClock) -> None:
        issue(memo="古い")
        clock.advance(minutes=1)
        issue(memo="新しい")
        html = browser.get("/console/licenses").get_data(as_text=True)
        assert html.index("新しい") < html.index("古い")

    def test_search_by_memo_and_status_is_kept_in_session(self, browser: Browser, issue: IssueFn) -> None:
        issue(memo="山田病院")
        suspended = issue(memo="山田クリニック")
        issue(memo="佐藤医院")
        post_form(browser, suspended, "suspend", confirmed=True)
        response = browser.post("/console/licenses/search", {"q": "山田", "status": "active"})
        assert (response.status_code, response.headers["Location"]) == (302, "/console/licenses")
        html = browser.get("/console/licenses").get_data(as_text=True)
        assert "山田病院" in html and "山田クリニック" not in html and "佐藤医院" not in html
        assert 'value="山田"' in html

    def test_search_by_key_part_never_puts_key_in_url(self, browser: Browser, issue: IssueFn) -> None:
        detail = issue(memo="対象")
        issue(memo="対象外")
        key = key_on(browser, detail)
        response = browser.post("/console/licenses/search", {"q": key[3:15].upper(), "status": ""})
        assert key[3:15].lower() not in response.headers["Location"].lower()
        html = browser.get("/console/licenses").get_data(as_text=True)
        assert ">対象<" in html and "対象外" not in html
        assert not re.search(r'href="[^"]*lk_', html)

    def test_empty_search_clears_the_filter(self, browser: Browser, issue: IssueFn) -> None:
        issue(memo="A社")
        issue(memo="B社")
        browser.post("/console/licenses/search", {"q": "A社"})
        browser.post("/console/licenses/search", {"q": "", "status": ""})
        html = browser.get("/console/licenses").get_data(as_text=True)
        assert "A社" in html and "B社" in html

    def test_no_match_message(self, browser: Browser, issue: IssueFn) -> None:
        issue(memo="A社")
        browser.post("/console/licenses/search", {"q": "存在しない"})
        assert "該当するライセンスはありません" in browser.get("/console/licenses").get_data(as_text=True)

    def test_invalid_search_is_shown_with_errors(self, browser: Browser) -> None:
        response = browser.post("/console/licenses/search", {"q": "あ" * 101})
        assert response.status_code == 400
        assert "検索語は 100 文字以内" in response.get_data(as_text=True)

    def test_pages_of_20_with_numbered_pager(self, browser: Browser, repo: SqliteRepository) -> None:
        seed(repo, 41)
        first = browser.get("/console/licenses").get_data(as_text=True)
        assert rows(first) == [f"契約{index:02d}" for index in range(40, 20, -1)]
        assert "全 41 件中 1〜20 件" in first
        assert 'href="/console/licenses?page=2"' in first and 'href="/console/licenses?page=3"' in first
        assert re.search(r'<li class="page-item active" aria-current="page">\s*<span class="page-link">1</span>', first)
        assert re.search(r'<li class="page-item disabled">\s*<span class="page-link">前へ</span>', first)
        last = browser.get("/console/licenses", page="3").get_data(as_text=True)
        assert rows(last) == ["契約00"] and "全 41 件中 41〜41 件" in last
        assert re.search(r'<li class="page-item disabled">\s*<span class="page-link">次へ</span>', last)
        assert 'href="/console/licenses?page=2"' in last

    def test_pager_skips_distant_pages(self, browser: Browser, repo: SqliteRepository) -> None:
        seed(repo, 20 * 12)
        html = browser.get("/console/licenses", page="6").get_data(as_text=True)
        numbers = re.findall(r'class="page-link"[^>]*>(\d+|…)<', html)
        assert numbers == ["1", "…", "4", "5", "6", "7", "8", "…", "12"]

    def test_page_beyond_the_last_shows_the_last_page(self, browser: Browser, issue: IssueFn) -> None:
        issue(memo="唯一")
        html = browser.get("/console/licenses", page="5").get_data(as_text=True)
        assert rows(html) == ["唯一"]


def seed(repo: SqliteRepository, count: int) -> None:
    """Licenses "契約00", "契約01", ... issued one second apart, straight into storage (the pages are what is tested)."""
    width = len(str(count - 1)) if count > 100 else 2
    for index in range(count):
        created = (datetime(2026, 9, 1, tzinfo=UTC) + timedelta(seconds=index)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        ctx = AuditContext(Operator("seed@example.com", "seed"), f"seed-{index}", None, created)
        repo.create_audited(f"lk_{index:032x}", 0, f"契約{index:0{width}d}", ctx)


def rows(html: str) -> list[str]:
    return re.findall(r'<td><a class="fw-semibold text-decoration-none" href="[^"]+">([^<]+)</a></td>', html)


class TestSort:
    @pytest.fixture
    def three(self, browser: Browser, issue: IssueFn, repo: SqliteRepository, clock: FixedClock) -> None:
        for memo, uses in [("二回", 2), ("なし", 0), ("一回", 1)]:
            clock.advance(minutes=1)
            detail = issue(memo=memo)
            for day in range(uses):
                repo.try_insert_within_limit(key_on(browser, detail), f"2026-09-0{day + 1}T00:00:00.000Z", month_period_of(2026, 9))

    @pytest.mark.usefixtures("three")
    @pytest.mark.parametrize(
        ("sort", "order", "expected"),
        [
            ("used", "desc", ["二回", "一回", "なし"]),
            ("used", "asc", ["なし", "一回", "二回"]),
            ("created_at", "asc", ["二回", "なし", "一回"]),
            ("created_at", "desc", ["一回", "なし", "二回"]),
        ],
    )
    def test_sorts_by_usage_and_issue_time(self, browser: Browser, sort: str, order: str, expected: list[str]) -> None:
        html = browser.get("/console/licenses", sort=sort, order=order).get_data(as_text=True)
        assert rows(html) == expected

    @pytest.mark.usefixtures("three")
    def test_headers_link_to_sorting_and_toggle_the_current_column(self, browser: Browser) -> None:
        default = browser.get("/console/licenses").get_data(as_text=True)
        assert re.search(r'<th[^>]*aria-sort="descending"[^>]*>\s*<a [^>]*href="/console/licenses\?sort=created_at&amp;order=asc"', default)
        assert 'href="/console/licenses?sort=used&amp;order=desc"' in default
        by_usage = browser.get("/console/licenses", sort="used", order="desc").get_data(as_text=True)
        assert 'href="/console/licenses?sort=used&amp;order=asc"' in by_usage
        assert re.search(r'<th[^>]*aria-sort="descending"[^>]*>\s*<a [^>]*sort=used', by_usage)
        assert "ライセンシー</th>" in by_usage and "状態</th>" in by_usage  # not sortable

    def test_pager_keeps_the_sort(self, browser: Browser, repo: SqliteRepository) -> None:
        seed(repo, 21)
        html = browser.get("/console/licenses", sort="created_at", order="asc").get_data(as_text=True)
        assert rows(html)[0] == "契約00"
        assert 'href="/console/licenses?page=2&amp;sort=created_at&amp;order=asc"' in html
        second = browser.get("/console/licenses", page="2", sort="created_at", order="asc").get_data(as_text=True)
        assert rows(second) == ["契約20"]

    def test_search_keeps_the_sort(self, browser: Browser, issue: IssueFn) -> None:
        html = browser.get("/console/licenses", sort="used", order="asc").get_data(as_text=True)
        assert re.search(r'<input type="hidden" name="sort" value="used">', html)
        response = browser.post("/console/licenses/search", {"q": "", "status": "", "sort": "used", "order": "asc"})
        assert response.headers["Location"] == "/console/licenses?sort=used&order=asc"

    def test_unknown_sort_falls_back_to_newest_first(self, browser: Browser, issue: IssueFn, clock: FixedClock) -> None:
        issue(memo="古い")
        clock.advance(minutes=1)
        issue(memo="新しい")
        html = browser.get("/console/licenses", sort="license_key", order="asc").get_data(as_text=True)
        assert rows(html) == ["古い", "新しい"]  # the order is kept, the unknown column is not


class TestIssue:
    def test_issue_shows_detail_with_key_copy_and_success(self, browser: Browser, repo: SqliteRepository) -> None:
        detail = detail_path(issue_via_console(browser, "新規契約"))
        html = browser.get(detail).get_data(as_text=True)
        assert "ライセンスを発行しました。" in html
        assert re.search(r'id="license-key" class="key">lk_[0-9a-f]{32}<', html)
        assert 'data-copy-target="license-key"' in html
        assert "新規契約" in html
        assert repo.count_audits("issue") == 1

    def test_issued_license_is_unlimited(self, browser: Browser, repo: SqliteRepository) -> None:
        detail = issue_via_console(browser, "上限なし")
        key = key_on(browser, detail_path(detail))
        assert repo.find(key).monthly_limit == 0  # type: ignore[union-attr]
        audit = browser.get(detail_path(detail)).get_data(as_text=True).split("操作の記録", 1)[1]
        assert "ライセンシー: 「上限なし」" in audit and "月間上限" not in audit

    def test_success_message_is_shown_once(self, browser: Browser, issue: IssueFn) -> None:
        detail = issue()
        browser.get(detail)
        assert "ライセンスを発行しました。" not in browser.get(detail).get_data(as_text=True)

    def test_invalid_input_is_redisplayed_per_field(self, browser: Browser, repo: SqliteRepository) -> None:
        page = browser.get("/console/licenses/new").get_data(as_text=True)
        memo = "あ" * 201
        response = browser.post("/console/licenses", {"memo": memo, "request_id": field_value(page, "request_id")})
        html = response.get_data(as_text=True)
        assert response.status_code == 400
        assert "ライセンシーは 200 文字以内で入力してください。" in html
        assert f'value="{memo}"' in html
        assert repo.count_licenses() == 0

    def test_double_submit_issues_once_and_shows_first(self, browser: Browser, repo: SqliteRepository) -> None:
        page = browser.get("/console/licenses/new").get_data(as_text=True)
        form = {"memo": "二重", "request_id": field_value(page, "request_id")}
        first = browser.post("/console/licenses", form)
        second = browser.post("/console/licenses", form)
        assert second.headers["Location"] == first.headers["Location"]
        assert repo.count_licenses() == 1
        assert "この操作はすでに実行されています。" in browser.get(second.headers["Location"]).get_data(as_text=True)

    def test_limit_posted_without_the_form_is_still_validated(self, browser: Browser, repo: SqliteRepository) -> None:
        page = browser.get("/console/licenses/new").get_data(as_text=True)
        response = browser.post("/console/licenses", {"monthly_limit": "-5", "memo": "", "request_id": field_value(page, "request_id")})
        assert response.status_code == 400
        assert repo.count_licenses() == 0

    def test_missing_request_id_is_rejected(self, browser: Browser, repo: SqliteRepository) -> None:
        response = browser.post("/console/licenses", {"memo": ""})
        assert response.status_code == 400
        assert repo.count_licenses() == 0


class TestDetailAndChanges:
    def test_detail_shows_all_properties(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue(memo="詳細")
        repo.try_insert_within_limit(key_on(browser, detail), "2026-09-10T00:00:00.000Z", month_period_of(2026, 9))
        html = browser.get(detail).get_data(as_text=True)
        for text in ("詳細", "有効", "1 回", "2026-09-15 12:00:00", "発行日時", "最終更新日時"):
            assert text in html

    def test_month_selection_shows_that_month(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue()
        repo.try_insert_within_limit(key_on(browser, detail), "2026-08-10T00:00:00.000Z", month_period_of(2026, 8))
        assert "0 回" in browser.get(detail).get_data(as_text=True)
        html = browser.get(detail, month="2026-08").get_data(as_text=True)
        assert "利用状況（2026-08）" in html and "1 回" in html
        bad = browser.get(detail, month="2026-13")
        assert bad.status_code == 400 and "対象月は YYYY-MM" in bad.get_data(as_text=True)

    def test_limit_route_still_changes_the_limit_without_showing_it(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue(monthly_limit=10)
        response = post_limit(browser, detail, "20")
        assert response.headers["Location"] == detail
        assert repo.find(key_on(browser, detail)).monthly_limit == 20  # type: ignore[union-attr]
        audit = browser.get(detail).get_data(as_text=True).split("操作の記録", 1)[1]
        assert "月間上限の変更" in audit and "月間上限:" not in audit
        assert "operator@example.com" in audit

    def test_limit_below_current_usage_asks_for_confirmation(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue(monthly_limit=10)
        key = key_on(browser, detail)
        for minute in range(3):
            repo.try_insert_within_limit(key, f"2026-09-10T00:0{minute}:00.000Z", month_period_of(2026, 9))
        page = post_limit(browser, detail, "2")
        html = page.get_data(as_text=True)
        assert page.status_code == 200
        assert "当月の利用回数（3 回）より小さい値です" in html and "残り回数は 0" in html
        assert repo.find(key).monthly_limit == 10  # type: ignore[union-attr]
        confirmed = browser.post(
            f"{detail}/limit",
            {"monthly_limit": "2", "confirmed": "1", "request_id": field_value(html, "request_id")},
        )
        assert confirmed.headers["Location"] == detail
        assert repo.find(key).monthly_limit == 2  # type: ignore[union-attr]

    def test_invalid_limit_changes_nothing(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue(monthly_limit=10)
        response = post_limit(browser, detail, "abc")
        assert response.status_code == 400
        assert "入力内容に誤りがあります" in response.get_data(as_text=True)
        assert repo.find(key_on(browser, detail)).monthly_limit == 10  # type: ignore[union-attr]

    def test_suspend_and_activate_require_confirmation(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue()
        key = key_on(browser, detail)
        confirm = post_form(browser, detail, "suspend")
        html = confirm.get_data(as_text=True)
        assert "このライセンスを停止します" in html
        assert repo.find(key).status.value == "active"  # type: ignore[union-attr]
        done = browser.post(f"{detail}/suspend", {"confirmed": "1", "request_id": field_value(html, "request_id")})
        assert done.headers["Location"] == detail
        page = browser.get(detail).get_data(as_text=True)
        assert "ライセンスを停止しました。" in page and "停止中" in page and "再開する" in page
        confirm = post_form(browser, detail, "activate")
        assert "このライセンスを再開します" in confirm.get_data(as_text=True)
        post_form(browser, detail, "activate", confirmed=True)
        assert repo.find(key).status.value == "active"  # type: ignore[union-attr]

    def test_update_memo(self, browser: Browser, issue: IssueFn) -> None:
        detail = issue(memo="旧メモ")
        post_form(browser, detail, "memo", {"memo": "新メモ"})
        html = browser.get(detail).get_data(as_text=True)
        assert "ライセンシーを変更しました。" in html and "ライセンシー: 「旧メモ」" in html and "ライセンシー: 「新メモ」" in html

    def test_invalid_memo_changes_nothing(self, browser: Browser, issue: IssueFn) -> None:
        detail = issue(memo="元")
        response = post_form(browser, detail, "memo", {"memo": "改行\nあり"})
        assert response.status_code == 400
        assert "ライセンシーに改行や制御文字は使えません。" in response.get_data(as_text=True)

    def test_change_without_csrf_token_is_rejected(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue(memo="元")
        request_id = form_field(browser.get(detail).get_data(as_text=True), f"{detail}/memo", "request_id")
        response = browser.post(f"{detail}/memo", {"memo": "書き換え", "request_id": request_id}, csrf=False)
        assert response.status_code == 403
        assert "操作は実行されていません" in response.get_data(as_text=True)
        assert repo.find(key_on(browser, detail)).memo == "元"  # type: ignore[union-attr]

    def test_change_from_another_site_is_rejected(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue()
        request_id = form_field(browser.get(detail).get_data(as_text=True), f"{detail}/suspend", "request_id")
        response = browser.post(
            f"{detail}/suspend", {"confirmed": "1", "request_id": request_id}, origin="https://evil.example.com"
        )
        assert response.status_code == 403
        assert repo.find(key_on(browser, detail)).status.value == "active"  # type: ignore[union-attr]

    def test_double_submitted_change_runs_once(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue(memo="元")
        request_id = form_field(browser.get(detail).get_data(as_text=True), f"{detail}/memo", "request_id")
        browser.post(f"{detail}/memo", {"memo": "一回目", "request_id": request_id})
        second = browser.post(f"{detail}/memo", {"memo": "二回目", "request_id": request_id})
        assert second.headers["Location"] == detail
        assert "この操作はすでに実行されています。" in browser.get(detail).get_data(as_text=True)
        assert repo.find(key_on(browser, detail)).memo == "一回目"  # type: ignore[union-attr]

    def test_change_to_unknown_license_is_not_found(self, browser: Browser) -> None:
        response = browser.post(
            "/console/licenses/lic_0000000000000000/limit", {"monthly_limit": "5", "request_id": "req-unknown-1"}
        )
        assert response.status_code == 404
        assert "ライセンスが見つかりません" in response.get_data(as_text=True)

    def test_no_delete_operation_exists(self, browser: Browser, issue: IssueFn) -> None:
        detail = issue()
        assert "削除" not in browser.get(detail).get_data(as_text=True)
        assert browser.post(f"{detail}/delete", {"request_id": "req-delete-1"}).status_code in {404, 405}


class TestUsageLogs:
    def seed(self, browser: Browser, detail: str, repo: SqliteRepository, times: list[str]) -> None:
        key = key_on(browser, detail)
        for used_at in times:
            period = month_period_of(int(used_at[:4]), int(used_at[5:7]))
            assert repo.try_insert_within_limit(key, used_at, period).inserted

    def test_defaults_to_current_month_oldest_first_in_jst(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue(monthly_limit=100)
        self.seed(browser, detail, repo, ["2026-08-31T14:59:00.000Z", "2026-08-31T15:00:00.000Z", "2026-09-10T01:00:00.000Z"])
        html = browser.get(f"{detail}/usage").get_data(as_text=True)
        assert "2026-08-31 23:59:00" not in html
        assert html.index("2026-09-01 00:00:00") < html.index("2026-09-10 10:00:00")
        assert 'value="2026-09-01"' in html and 'value="2026-09-30"' in html

    def test_explicit_range_includes_end_day(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue(monthly_limit=100)
        self.seed(browser, detail, repo, ["2026-08-31T14:59:00.000Z", "2026-09-01T14:59:00.000Z", "2026-09-01T15:00:00.000Z"])
        html = browser.get(f"{detail}/usage", **{"from": "2026-08-31", "to": "2026-09-01"}).get_data(as_text=True)
        assert "2026-08-31 23:59:00" in html and "2026-09-01 23:59:00" in html
        assert "2026-09-02 00:00:00" not in html

    def test_more_than_100_entries_continue_on_next_page(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue(monthly_limit=1000)
        self.seed(browser, detail, repo, [f"2026-09-10T{m // 60:02d}:{m % 60:02d}:00.000Z" for m in range(101)])
        html = browser.get(f"{detail}/usage").get_data(as_text=True)
        assert html.count("<tr><td>") == 100
        link = re.search(r'href="([^"]*after_id=[^"]*)">続きを表示', html)
        assert link
        rest = browser.client.get(link.group(1).replace("&amp;", "&"), headers=browser.headers(), base_url=browser.host)
        rest_html = rest.get_data(as_text=True)
        assert rest_html.count("<tr><td>") == 1 and "続きを表示" not in rest_html

    def test_invalid_range_shows_errors_without_history(self, browser: Browser, issue: IssueFn, repo: SqliteRepository) -> None:
        detail = issue(monthly_limit=100)
        self.seed(browser, detail, repo, ["2026-09-10T01:00:00.000Z"])
        reversed_range = browser.get(f"{detail}/usage", **{"from": "2026-09-10", "to": "2026-09-01"})
        html = reversed_range.get_data(as_text=True)
        assert reversed_range.status_code == 400
        assert "開始日は終了日以前の日付を指定してください。" in html and "2026-09-10 10:00:00" not in html
        malformed = browser.get(f"{detail}/usage", **{"from": "2026/09/01"})
        assert malformed.status_code == 400 and "開始日は YYYY-MM-DD" in malformed.get_data(as_text=True)

    def test_empty_period_message(self, browser: Browser, issue: IssueFn) -> None:
        detail = issue()
        assert "この期間の利用はありません。" in browser.get(f"{detail}/usage").get_data(as_text=True)
