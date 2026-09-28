"""Run the console's production SQL against the docker compose dev server's local D1 (miniflare).

Checks what the SQLite test double cannot prove about D1 itself:
- a write whose audit insert hits a reused request_id rolls back entirely (the license is not changed)
- the error text used to detect a reused request_id
- json_object() output, NULLIF for a missing source IP, and instr() search on Japanese text

`wrangler d1 execute` cannot bind parameters, so this script inlines the (script-generated) values as SQL
literals. The Worker's own binding path is covered by scripts/console_smoke.sh.

Usage: docker compose up -d app && uv run python scripts/console_d1_check.py
Leaves a few test licenses and audit records in the local D1 (audit records cannot be deleted by design).
"""

import json
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from license_server.domain.period import month_period_of  # noqa: E402
from license_server.domain.types import AuditContext, LicenseSearch, LicenseStatus, Operator  # noqa: E402
from license_server.repository import sql  # noqa: E402


def literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def inline(statements: list[sql.Statement]) -> str:
    def render(query: str, params: tuple[Any, ...]) -> str:
        return re.sub(r"\?(\d+)", lambda m: literal(params[int(m.group(1)) - 1]), query)

    return "; ".join(render(query, params) for query, params in statements)


def execute(statements: list[sql.Statement]) -> tuple[bool, Any]:
    completed = subprocess.run(
        ["docker", "compose", "exec", "-T", "app", "npx", "wrangler", "d1", "execute", "DB", "--local", "--json",
         "--command", inline(statements)],
        capture_output=True, text=True, check=False, timeout=120, stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        return False, completed.stdout + completed.stderr
    return True, [result["results"] for result in json.loads(completed.stdout)]


def main() -> None:
    key = "lk_" + secrets.token_hex(16)
    tag = secrets.token_hex(4)
    memo = f"山田病院テスト{tag}"
    operator = Operator(email="d1-check@example.com", subject="d1-check")

    def ctx(request_id: str, ip: str | None = None) -> AuditContext:
        return AuditContext(operator, request_id, ip, "2026-09-28T00:00:00.000Z")

    issue_id = f"d1check-{tag}-issue"
    september = month_period_of(2026, 9)
    audit_query = (
        "SELECT action, before_json, after_json, source_ip FROM console_audit_logs WHERE license_key = ?1 ORDER BY id"
    )

    ok, issued = execute(sql.create_audited(key, 5, memo, ctx(issue_id)))
    check(ok and issued[0][0]["public_id"].startswith("lic_"), "issue returns a public_id")
    ok, error = execute(sql.update_limit_audited(key, 7, ctx(issue_id)))
    check(not ok, "reused request_id fails")
    check(sql.is_duplicate_request(Exception(error)), "reused request_id error text is detectable")
    _, current = execute([(sql.SELECT_LICENSE, (key,))])
    check(current[0][0]["monthly_limit"] == 5, "failed write rolled back (limit unchanged)")
    ok, limited = execute(sql.update_limit_audited(key, 9, ctx(f"d1check-{tag}-limit", ip="203.0.113.9")))
    check(ok and limited[-1][0]["monthly_limit"] == 9, "update_limit applies")
    ok, noop = execute(sql.set_status_audited(key, LicenseStatus.ACTIVE, ctx(f"d1check-{tag}-noop")))
    check(ok and noop[-1] == [], "status change to the current status returns no row")
    _, audits = execute([(audit_query, (key,))])
    rows = audits[0]
    check([row["action"] for row in rows] == ["issue", "update_limit"], "only real changes are audited")
    check(json.loads(rows[0]["after_json"]) == {"monthly_limit": 5, "memo": memo}, "json_object keeps int and text")
    check("5.0" not in rows[0]["after_json"], "monthly_limit is an integer in JSON")
    check((rows[0]["source_ip"], rows[1]["source_ip"]) == (None, "203.0.113.9"), "missing source IP is NULL")
    check(json.loads(rows[1]["before_json"]) == {"monthly_limit": 5}, "before value comes from the row")
    _, by_memo = execute([(sql.SEARCH_LICENSES, sql.search_params(LicenseSearch(f"病院テスト{tag}", None), september, 51, 0))])
    check([r["license_key"] for r in by_memo[0]] == [key], "instr finds a Japanese memo substring")
    _, by_key = execute([(sql.SEARCH_LICENSES, sql.search_params(LicenseSearch(key[3:20].upper(), None), september, 51, 0))])
    check(key in [r["license_key"] for r in by_key[0]], "key search ignores case")
    print("console D1 check passed")


def check(condition: bool, label: str) -> None:
    if not condition:
        print(f"FAILED: {label}", file=sys.stderr)
        sys.exit(1)
    print(f"ok: {label}")


if __name__ == "__main__":
    main()
