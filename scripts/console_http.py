"""Minimal browser for the admin console on the docker compose dev server (stdlib only).

Cookies are handled by hand to keep the client stdlib-only and predictable. Nothing printed here contains
license keys, tokens or cookie values.
"""

import http.client
import re
import sys
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit


@dataclass
class Page:
    status: int
    headers: dict[str, str]
    body: str

    @property
    def location(self) -> str:
        return self.headers.get("location", "")

    def field(self, name: str, action: str | None = None) -> str:
        html = self.body
        if action is not None:
            form = re.search(rf'<form[^>]*action="{re.escape(action)}".*?</form>', html, flags=re.DOTALL)
            if form is None:
                fail(f"no form posting to {action}")
            html = form.group(0)
        match = re.search(rf'name="{name}" value="([^"]*)"', html)
        if match is None:
            fail(f"no {name} field on page")
        return match.group(1)


class ConsoleBrowser:
    def __init__(self, base_url: str) -> None:
        parts = urlsplit(base_url)
        self.base_url = base_url.rstrip("/")
        self.host, self.port = parts.hostname or "localhost", parts.port or 80
        self.cookies: dict[str, str] = {}

    def get(self, path: str) -> Page:
        return self._request("GET", path)

    def post(self, path: str, form: dict[str, str], csrf: str | None = None, origin: str | None = None) -> Page:
        data = dict(form)
        if csrf is not None:
            data["csrf_token"] = csrf
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Origin": origin or self.base_url}
        return self._request("POST", path, urlencode(data).encode(), headers)

    def csrf(self) -> str:
        return self.get("/console/licenses").field("csrf_token")

    def _request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> Page:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=30)
        all_headers = dict(headers or {})
        if self.cookies:
            all_headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in self.cookies.items())
        connection.request(method, path, body=body, headers=all_headers)
        response = connection.getresponse()
        page = Page(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read().decode())
        for header in response.headers.get_all("Set-Cookie") or []:
            name, _, rest = header.partition("=")
            value = rest.split(";", 1)[0]
            if value and "max-age=0" not in header.lower():
                self.cookies[name] = value
            else:
                self.cookies.pop(name, None)
        connection.close()
        return page


def check(condition: bool, label: str) -> None:
    if not condition:
        fail(label)
    print(f"ok   {label}")


def fail(label: str) -> None:
    print(f"FAIL {label}", file=sys.stderr)
    sys.exit(1)
