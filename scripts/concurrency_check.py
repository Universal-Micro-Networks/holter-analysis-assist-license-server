"""Concurrent-usage check against a running dev server (`docker compose up`) and local D1.

Sends REQUESTS parallel usage records to a license with monthly_limit LIMIT and verifies that exactly
LIMIT succeed, the rest are rejected with monthly_limit_reached, and no more than LIMIT logs exist.

    uv run python scripts/concurrency_check.py
"""

import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8787")
LIMIT = 10
REQUESTS = 30


def admin_token() -> str:
    if token := os.environ.get("ADMIN_API_TOKEN"):
        return token
    match = re.search(r"^ADMIN_API_TOKEN=(.*)$", Path(".dev.vars").read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        sys.exit("ADMIN_API_TOKEN is not set and not found in .dev.vars")
    return match.group(1).strip()


def call(method: str, path: str, token: str | None = None, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def main() -> int:
    status, _ = call("GET", "/healthz")
    print(f"healthz: {status}")
    if status != 200:
        return 1

    admin = admin_token()
    status, body = call("POST", "/v1/admin/licenses", admin, {"monthly_limit": LIMIT})
    if status != 201:
        print(f"could not issue license: {status} {body}")
        return 1
    key = body["data"]["license_key"]

    barrier = threading.Barrier(REQUESTS)

    def record(_: int) -> str:
        barrier.wait()
        status, body = call("POST", "/v1/usage", key)
        return str(status) if body["ok"] else f"{status} {body['error']['code']}"

    with ThreadPoolExecutor(max_workers=REQUESTS) as pool:
        outcomes = Counter(pool.map(record, range(REQUESTS)))
    print(f"outcomes of {REQUESTS} parallel records (limit {LIMIT}): {dict(outcomes)}")

    _, logs = call(
        "POST",
        "/v1/admin/usage/logs",
        admin,
        {"license_key": key, "from": "2000-01-01T00:00:00Z", "to": "2100-01-01T00:00:00Z", "limit": 1000},
    )
    stored = len(logs["data"]["entries"])
    print(f"stored usage logs: {stored}")

    expected = Counter({"201": LIMIT, "403 monthly_limit_reached": REQUESTS - LIMIT})
    if outcomes == expected and stored == LIMIT:
        print("concurrency check passed")
        return 0
    print("concurrency check FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
