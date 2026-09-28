"""Operator console pages under /console: server-rendered Japanese HTML, POST for every change.

License keys never appear in URLs: pages address licenses by `public_id`, and search input (which may
contain a key) is posted and kept in the session instead of the query string.
"""

import re
import uuid
from collections.abc import Mapping
from typing import Any

from flask import Blueprint, Response, g, redirect, render_template, request, url_for

from license_server.console import messages
from license_server.console.access import AccessDenied
from license_server.console.dependencies import ConsoleDependencies, current_console
from license_server.console.forms import (
    FormInvalid,
    parse_after_id,
    parse_date_range,
    parse_issue,
    parse_limit,
    parse_memo,
    parse_month,
    parse_page,
    parse_search,
    parse_sort,
)
from license_server.console.security import is_local_http, is_loopback_host, verify_csrf
from license_server.console.session import ConsoleSession, SessionExpired, cookie_name
from license_server.domain.errors import ErrorCode, ServiceError
from license_server.domain.period import format_jst
from license_server.domain.types import (
    AuditAction,
    AuditContext,
    AuditValues,
    License,
    LicenseSearch,
    LicenseSort,
    LicenseStatus,
    Operator,
    SortOrder,
)
from license_server.http.access_log import remember_operator
from license_server.http.rate_limit import enforce_rate_limit
from license_server.repository.base import DuplicateSubmission
from license_server.services.console_service import ConsoleService

console = Blueprint("console", __name__, url_prefix="/console", template_folder="templates")

ACCESS_LOGOUT_PATH = "/cdn-cgi/access/logout"
_PUBLIC_ENDPOINTS = {"console.signed_out"}
_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
_STATUS_LABELS = {LicenseStatus.ACTIVE: "有効", LicenseStatus.SUSPENDED: "停止中"}
_ACTION_LABELS = {
    AuditAction.ISSUE: "発行",
    AuditAction.UPDATE_LIMIT: "月間上限の変更",
    AuditAction.SUSPEND: "停止",
    AuditAction.ACTIVATE: "再開",
    AuditAction.UPDATE_MEMO: "ライセンシーの変更",
    AuditAction.SIGN_IN: "サインイン",
}
_FIELD_LABELS = {"monthly_limit": "月間上限", "memo": "ライセンシー", "status": "状態"}
# The monthly limit is not operated from the console; licenses it issues are unlimited.
_HIDDEN_FIELDS = {"monthly_limit"}


class NotSignedIn(Exception):
    """No valid Cloudflare Access assertion."""


class NotConfigured(Exception):
    """Neither Access nor the local development operator is available, or the session secret is missing."""


# ---------------------------------------------------------------- guard


@console.before_request
def guard() -> Response | None:
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    deps = current_console()
    enforce_rate_limit(deps.rate_limiter, _source_ip())
    if not deps.configured or deps.session_secret is None:
        raise NotConfigured()
    operator = _identify(deps)
    secure = not is_local_http(request.scheme, request.host)
    session = ConsoleSession.load(request.cookies.get(cookie_name(secure)), deps.session_secret)
    g.console_session, g.console_session_secret = session, deps.session_secret
    try:
        is_new = session.touch(operator, deps.clock())
    except SessionExpired:
        session.clear()
        return _redirect(_logout_url(deps))
    g.console_operator = operator
    remember_operator(operator.email)
    if is_new:
        deps.console.record_sign_in(deps.console.context(operator, f"sign-in-{uuid.uuid4().hex}", _source_ip()))
    if request.method == "POST":
        verify_csrf(
            request.form.get("csrf_token"),
            session.csrf_token,
            request.headers.get("Origin"),
            request.headers.get("Referer"),
            request.host_url,
        )
    return None


def _identify(deps: ConsoleDependencies) -> Operator:
    if deps.verifier is not None:
        token = request.headers.get("Cf-Access-Jwt-Assertion")
        if not token:
            raise NotSignedIn()
        try:
            return deps.verifier.verify(token)
        except AccessDenied as denial:
            raise NotSignedIn() from denial
    if deps.dev_operator is not None and is_loopback_host(request.host):
        return deps.dev_operator
    raise NotConfigured()


def _logout_url(deps: ConsoleDependencies) -> str:
    return ACCESS_LOGOUT_PATH if deps.verifier is not None else url_for("console.signed_out")


# ---------------------------------------------------------------- pages


@console.get("")
@console.get("/")
def index() -> Response:
    return _redirect(url_for("console.licenses"))


@console.get("/licenses")
def licenses() -> Response:
    sort, order = parse_sort(request.args.get("sort"), request.args.get("order"))
    return _render_list(parse_page(request.args.get("page")), sort, order)


@console.post("/licenses/search")
def search() -> Response:
    sort, order = parse_sort(request.form.get("sort"), request.form.get("order"))
    try:
        query, status = parse_search(request.form)
    except FormInvalid as invalid:
        entered = (request.form.get("q", ""), request.form.get("status", ""))
        return _render_list(1, sort, order, errors=invalid.errors, entered=entered, status=400)
    _session().set_search(query, status)
    return _redirect(url_for("console.licenses", **_sort_args(sort, order)))


@console.get("/licenses/new")
def new_license() -> Response:
    return _render("license_new.html", values={}, errors={}, request_id=_new_request_id())


@console.post("/licenses")
def create_license() -> Response:
    request_id = _form_request_id()
    try:
        form = parse_issue(request.form)
    except FormInvalid as invalid:
        return _render(
            "license_new.html", status=400, values=request.form, errors=invalid.errors, request_id=request_id
        )
    service = _service()
    try:
        issued = service.issue(form.monthly_limit, form.memo, _context(request_id))
    except DuplicateSubmission:
        return _duplicate(service.license_for_request(request_id))
    _session().flash(messages.issued())
    return _to_detail(issued)


@console.get("/licenses/<public_id>")
def license_detail(public_id: str) -> Response:
    errors: dict[str, str] = {}
    try:
        month = parse_month(request.args.get("month"), current_console().clock())
    except FormInvalid as invalid:
        errors, month = invalid.errors, None
    return _render_detail(public_id, month, errors=errors, status=400 if errors else 200)


@console.post("/licenses/<public_id>/limit")
def update_limit(public_id: str) -> Response:
    request_id = _form_request_id()
    try:
        limit = parse_limit(request.form)
    except FormInvalid as invalid:
        return _render_detail(public_id, None, errors=invalid.errors, values=request.form, status=400)
    service = _service()
    if not _confirmed():
        used = service.detail(public_id, None).summary.used
        if limit < used:
            return _render_confirm(
                title="月間上限回数の変更",
                message=f"月間上限回数を {limit:,} 回に変更します。",
                warning=(
                    f"当月の利用回数（{used:,} 回）より小さい値です。変更すると当月の残り回数は 0 になり、"
                    "以降の当月の利用は拒否されます。"
                ),
                action=url_for("console.update_limit", public_id=public_id),
                button="変更する",
                destructive=True,
                public_id=public_id,
                request_id=request_id,
                fields={"monthly_limit": str(limit)},
            )
    try:
        service.update_limit(public_id, limit, _context(request_id))
    except DuplicateSubmission:
        return _duplicate_on(public_id)
    return _done(public_id, messages.limit_updated(limit))


@console.post("/licenses/<public_id>/suspend")
def suspend(public_id: str) -> Response:
    return _change_status(public_id, LicenseStatus.SUSPENDED)


@console.post("/licenses/<public_id>/activate")
def activate(public_id: str) -> Response:
    return _change_status(public_id, LicenseStatus.ACTIVE)


@console.post("/licenses/<public_id>/memo")
def update_memo(public_id: str) -> Response:
    request_id = _form_request_id()
    try:
        memo = parse_memo(request.form)
    except FormInvalid as invalid:
        return _render_detail(public_id, None, errors=invalid.errors, values=request.form, status=400)
    try:
        _service().update_memo(public_id, memo, _context(request_id))
    except DuplicateSubmission:
        return _duplicate_on(public_id)
    return _done(public_id, messages.memo_updated())


@console.get("/licenses/<public_id>/usage")
def usage_logs(public_id: str) -> Response:
    service = _service()
    start_text, end_text = request.args.get("from"), request.args.get("to")
    values = {"from": start_text or "", "to": end_text or ""}
    try:
        start, end, period = parse_date_range(start_text, end_text, current_console().clock())
    except FormInvalid as invalid:
        license_ = service.license(public_id)
        return _render(
            "usage_logs.html", status=400, license=license_, entries=None, next_after=None,
            values=values, errors=invalid.errors,
        )
    license_, entries, next_after = service.usage_logs(public_id, period, parse_after_id(request.args.get("after_id")))
    return _render(
        "usage_logs.html", license=license_, entries=entries, next_after=next_after,
        values={"from": start.isoformat(), "to": end.isoformat()}, errors={},
    )


@console.post("/sign-out")
def sign_out() -> Response:
    _session().clear()
    return _redirect(_logout_url(current_console()))


@console.get("/signed-out")
def signed_out() -> Response:
    return _render("signed_out.html")


# ---------------------------------------------------------------- helpers


def _change_status(public_id: str, status: LicenseStatus) -> Response:
    request_id = _form_request_id()
    service = _service()
    suspending = status is LicenseStatus.SUSPENDED
    if not _confirmed():
        license_ = service.license(public_id)
        return _render_confirm(
            title="ライセンスの停止" if suspending else "ライセンスの再開",
            message=(
                "このライセンスを停止します。停止中は利用の記録と有効性の確認がすべて拒否されます。"
                if suspending
                else "このライセンスを再開します。再開すると利用の記録ができるようになります。"
            ),
            warning=None,
            action=url_for("console.suspend" if suspending else "console.activate", public_id=public_id),
            button="停止する" if suspending else "再開する",
            destructive=suspending,
            public_id=public_id,
            request_id=request_id,
            fields={},
            license=license_,
        )
    change = service.suspend if suspending else service.activate
    try:
        change(public_id, _context(request_id))
    except DuplicateSubmission:
        return _duplicate_on(public_id)
    return _done(public_id, messages.suspended() if suspending else messages.activated())


def _render_list(
    page_number: int,
    sort: LicenseSort,
    order: SortOrder,
    errors: Mapping[str, str] | None = None,
    entered: tuple[str, str] | None = None,
    status: int = 200,
) -> Response:
    query, license_status = _session().search()
    page = _service().search(LicenseSearch(query, license_status, page_number, sort, order))
    shown_query, shown_status = entered or (query or "", license_status.value if license_status else "")
    return _render(
        "licenses_list.html", status=status, page=page, query=shown_query, status_filter=shown_status,
        errors=errors or {}, sort=sort.value, order=order.value, sort_args=_sort_args(sort, order),
        pages=_page_window(page.page, page.page_count),
    )


def _sort_args(sort: LicenseSort, order: SortOrder) -> dict[str, Any]:
    """Query arguments that keep the list order; empty for the default so plain URLs stay plain."""
    if (sort, order) == (LicenseSort.CREATED_AT, SortOrder.DESC):
        return {}
    return {"sort": sort.value, "order": order.value}


def _page_window(page: int, page_count: int, radius: int = 2) -> list[int | None]:
    """Page numbers for the pager: first, last and `radius` around the current page; None marks a gap."""
    shown = sorted({1, page_count, *range(max(1, page - radius), min(page_count, page + radius) + 1)})
    window: list[int | None] = []
    previous = 0
    for number in shown:
        if number - previous == 2:
            window.append(previous + 1)
        elif number - previous > 2:
            window.append(None)
        window.append(number)
        previous = number
    return window


def _render_detail(
    public_id: str,
    month: tuple[int, int] | None,
    errors: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    status: int = 200,
) -> Response:
    detail = _service().detail(public_id, month)
    start = detail.summary.period.start_utc
    return _render(
        "license_detail.html", status=status, detail=detail, license=detail.license,
        selected_month=format_jst(start)[:7], errors=errors or {}, values=values or {},
    )


def _render_confirm(**context: Any) -> Response:
    return _render("confirm.html", **context)


def _render(template: str, status: int = 200, **context: Any) -> Response:
    session: ConsoleSession | None = g.get("console_session")
    html = render_template(
        f"console/{template}",
        operator=g.get("console_operator"),
        csrf_token=session.csrf_token if session else "",
        flashes=session.pop_flashes() if session else [],
        **context,
    )
    return Response(html, status=status, mimetype="text/html")


def _done(public_id: str, message: str) -> Response:
    _session().flash(message)
    return _redirect(url_for("console.license_detail", public_id=public_id))


def _duplicate(license_: License | None) -> Response:
    _session().flash(messages.DUPLICATE_SUBMISSION)
    if license_ is None or license_.public_id is None:
        return _redirect(url_for("console.licenses"))
    return _to_detail(license_)


def _duplicate_on(public_id: str) -> Response:
    _session().flash(messages.DUPLICATE_SUBMISSION)
    return _redirect(url_for("console.license_detail", public_id=public_id))


def _to_detail(license_: License) -> Response:
    return _redirect(url_for("console.license_detail", public_id=license_.public_id))


def _redirect(location: str) -> Response:
    return redirect(location, code=302)  # type: ignore[return-value]


def _confirmed() -> bool:
    return request.form.get("confirmed") == "1"


def _form_request_id() -> str:
    value = request.form.get("request_id", "")
    if not _REQUEST_ID.fullmatch(value):
        raise ServiceError(ErrorCode.INVALID_REQUEST)
    return value


def _new_request_id() -> str:
    return str(uuid.uuid4())


def _context(request_id: str) -> AuditContext:
    operator: Operator = g.console_operator
    return _service().context(operator, request_id, _source_ip())


def _service() -> ConsoleService:
    return current_console().console


def _session() -> ConsoleSession:
    session: ConsoleSession = g.console_session
    return session


def _source_ip() -> str | None:
    return request.headers.get("CF-Connecting-IP")


# ---------------------------------------------------------------- template helpers


@console.app_template_global("new_request_id")
def new_request_id_global() -> str:
    return _new_request_id()


@console.app_template_filter("jst")
def jst_filter(utc_text: str) -> str:
    return format_jst(utc_text)


@console.app_template_filter("status_label")
def status_label_filter(status: LicenseStatus) -> str:
    return _STATUS_LABELS[status]


@console.app_template_filter("number")
def number_filter(value: int) -> str:
    return f"{value:,}"


@console.app_template_filter("action_label")
def action_label_filter(action: AuditAction) -> str:
    return _ACTION_LABELS[action]


@console.app_template_filter("audit_values")
def audit_values_filter(values: AuditValues | None) -> str:
    if not values:
        return "—"
    parts = []
    for name, value in values.items():
        if name in _HIDDEN_FIELDS:
            continue
        if name == "status" and isinstance(value, str) and value in {s.value for s in LicenseStatus}:
            shown = _STATUS_LABELS[LicenseStatus(value)]
        elif name == "memo":
            shown = f"「{value}」" if value else "（なし）"
        elif isinstance(value, int):
            shown = f"{value:,}"
        else:
            shown = str(value)
        parts.append(f"{_FIELD_LABELS.get(name, name)}: {shown}")
    return "、".join(parts) or "—"
