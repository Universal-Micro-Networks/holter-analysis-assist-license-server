from flask.testing import FlaskClient

from license_server.domain.types import Operator
from tests.conftest import FixedClock
from tests.fakes.access import FakeCertsFetcher
from tests.fakes.sqlite_repository import SqliteRepository
from tests.integration.console.conftest import HOST, OPERATOR_EMAIL, Browser, ConsoleSetup, IssueFn

SIGN_IN_REQUIRED = "サインインが必要です"


class TestSignIn:
    def test_without_access_token_nothing_is_shown(self, browser: Browser, issue: IssueFn) -> None:
        issue(memo="秘密の顧客名")
        browser.signed_in = False
        for path in ["/console", "/console/licenses", "/console/licenses/new"]:
            response = browser.get(path)
            body = response.get_data(as_text=True)
            assert response.status_code == 403
            assert SIGN_IN_REQUIRED in body
            assert "秘密の顧客名" not in body and "lk_" not in body

    def test_invalid_token_gets_the_same_page(self, browser: Browser) -> None:
        browser.claims = {"aud": ["someone-else"]}
        response = browser.get("/console/licenses")
        assert response.status_code == 403
        assert SIGN_IN_REQUIRED in response.get_data(as_text=True)
        assert OPERATOR_EMAIL not in response.get_data(as_text=True)

    def test_top_redirects_to_list_showing_operator(self, browser: Browser) -> None:
        top = browser.get("/console")
        assert (top.status_code, top.headers["Location"]) == (302, "/console/licenses")
        page = browser.get("/console/licenses")
        assert page.status_code == 200
        assert OPERATOR_EMAIL in page.get_data(as_text=True)

    def test_first_request_records_sign_in_once(self, browser: Browser, repo: SqliteRepository) -> None:
        browser.get("/console/licenses")
        browser.get("/console/licenses")
        assert repo.count_audits("sign_in") == 1

    def test_different_operator_starts_new_session(self, browser: Browser, repo: SqliteRepository) -> None:
        token_before = browser.csrf_token()
        browser.claims = {"email": "other@example.com", "sub": "other-sub"}
        page = browser.get("/console/licenses").get_data(as_text=True)
        assert "other@example.com" in page
        assert browser.csrf_token() != token_before
        assert repo.count_audits("sign_in") == 2

    def test_session_cookie_is_host_only_secure_and_http_only(self, browser: Browser) -> None:
        cookie = browser.get("/console/licenses").headers["Set-Cookie"]
        assert cookie.startswith("__Host-console=")
        assert all(attribute in cookie for attribute in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/"))


class TestSessionEnd:
    def test_idle_over_eight_hours_sends_to_access_logout(self, browser: Browser, clock: FixedClock) -> None:
        browser.get("/console/licenses")
        clock.advance(hours=8, seconds=1)
        response = browser.get("/console/licenses")
        assert (response.status_code, response.headers["Location"]) == (302, "/cdn-cgi/access/logout")
        assert "__Host-console=;" in response.headers["Set-Cookie"]

    def test_activity_within_eight_hours_keeps_session(self, browser: Browser, clock: FixedClock) -> None:
        browser.get("/console/licenses")
        clock.advance(hours=8)
        assert browser.get("/console/licenses").status_code == 200

    def test_sign_out_clears_session_and_logs_out_of_access(self, browser: Browser) -> None:
        token = browser.csrf_token()
        response = browser.post("/console/sign-out", {"csrf_token": token})
        assert (response.status_code, response.headers["Location"]) == (302, "/cdn-cgi/access/logout")
        assert "__Host-console=;" in response.headers["Set-Cookie"]
        # The old token belonged to the ended session.
        assert browser.post("/console/licenses/search", {"csrf_token": token, "q": ""}).status_code == 403

    def test_sign_out_requires_csrf_token(self, browser: Browser) -> None:
        browser.get("/console/licenses")
        response = browser.post("/console/sign-out", {}, csrf=False)
        assert response.status_code == 403
        assert "操作は実行されていません" in response.get_data(as_text=True)


class TestConfiguration:
    def test_dev_operator_works_only_on_loopback(self, client: FlaskClient, clock: FixedClock, setup: ConsoleSetup) -> None:
        setup.verifier = None
        setup.dev_operator = Operator("dev@example.com", "dev")
        local = Browser(client, clock, host="http://localhost:8787")
        local.signed_in = False
        page = local.get("/console/licenses")
        assert page.status_code == 200 and "dev@example.com" in page.get_data(as_text=True)
        assert "Strict-Transport-Security" not in page.headers
        remote = Browser(client, clock)
        remote.signed_in = False
        response = remote.get("/console/licenses")
        assert response.status_code == 503
        assert "管理画面が設定されていません" in response.get_data(as_text=True)

    def test_local_http_uses_a_cookie_safari_keeps(self, client: FlaskClient, clock: FixedClock, setup: ConsoleSetup, repo: SqliteRepository) -> None:
        setup.verifier = None
        setup.dev_operator = Operator("dev@example.com", "dev")
        local = Browser(client, clock, host="http://localhost:8787")
        local.signed_in = False
        cookie = local.get("/console/licenses").headers["Set-Cookie"]
        assert cookie.startswith("console-local=") and "Secure" not in cookie
        assert local.post("/console/licenses/search", {"q": "", "status": ""}).status_code == 302
        assert repo.count_audits("sign_in") == 1
        signed_out = local.post("/console/sign-out", {})
        assert signed_out.headers["Set-Cookie"].startswith("console-local=;")

    def test_dev_sign_out_goes_to_local_signed_out_page(self, client: FlaskClient, clock: FixedClock, setup: ConsoleSetup) -> None:
        setup.verifier = None
        setup.dev_operator = Operator("dev@example.com", "dev")
        local = Browser(client, clock, host="http://localhost:8787")
        response = local.post("/console/sign-out", {})
        assert response.headers["Location"] == "/console/signed-out"
        page = local.client.get("/console/signed-out", base_url=local.host)
        assert page.status_code == 200 and "サインアウトしました" in page.get_data(as_text=True)

    def test_no_sign_in_method_is_not_configured(self, browser: Browser, setup: ConsoleSetup) -> None:
        setup.verifier = None
        response = browser.get("/console/licenses")
        assert response.status_code == 503
        assert "管理画面が設定されていません" in response.get_data(as_text=True)

    def test_missing_session_secret_is_not_configured(self, browser: Browser, setup: ConsoleSetup) -> None:
        setup.session_secret = None
        assert browser.get("/console/licenses").status_code == 503


class TestErrors:
    def test_rate_limited_page(self, browser: Browser, setup: ConsoleSetup) -> None:
        setup.limiter.allow = 0
        response = browser.get("/console/licenses")
        assert response.status_code == 429
        assert "しばらく待ってから" in response.get_data(as_text=True)

    def test_unknown_console_path_is_html_404(self, browser: Browser) -> None:
        response = browser.get("/console/nothing-here")
        assert response.status_code == 404
        assert response.mimetype == "text/html"
        assert "Content-Security-Policy" in response.headers

    def test_unknown_license_is_html_404(self, browser: Browser) -> None:
        response = browser.get("/console/licenses/lic_0000000000000000")
        assert response.status_code == 404
        assert "ライセンスが見つかりません" in response.get_data(as_text=True)

    def test_storage_outage_shows_temporary_failure_without_details(self, browser: Browser, repo: SqliteRepository) -> None:
        browser.get("/console/licenses")
        repo.unavailable = True
        response = browser.get("/console/licenses")
        body = response.get_data(as_text=True)
        assert response.status_code == 503
        assert "操作は完了していません" in body
        assert "simulated" not in body and "Traceback" not in body

    def test_access_keys_unavailable_is_temporary_failure(self, browser: Browser, fetcher: FakeCertsFetcher) -> None:
        fetcher.fail = True
        response = browser.get("/console/licenses")
        assert response.status_code == 503
        assert "操作は完了していません" in response.get_data(as_text=True)

    def test_api_errors_stay_json(self, client: FlaskClient) -> None:
        response = client.get("/v1/nothing-here", base_url=HOST)
        assert response.status_code == 404
        assert response.get_json()["ok"] is False

    def test_security_headers_on_every_console_response(self, browser: Browser) -> None:
        responses = [browser.get("/console/licenses"), browser.get("/console/nothing")]
        browser.signed_in = False
        responses.append(browser.get("/console/licenses"))
        for response in responses:
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            assert response.headers["X-Frame-Options"] == "DENY"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Referrer-Policy"] == "same-origin"
            assert response.headers["Strict-Transport-Security"] == "max-age=31536000"

    def test_healthz_and_api_have_no_console_headers(self, client: FlaskClient) -> None:
        response = client.get("/healthz", base_url=HOST)
        assert "Content-Security-Policy" not in response.headers
