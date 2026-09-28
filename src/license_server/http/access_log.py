import json
import time

from flask import Flask, Response, g, request

from license_server.domain.license_key import key_fingerprint


def remember_license_key(license_key: str) -> None:
    g.key_fingerprint = key_fingerprint(license_key)


def remember_operator(email: str) -> None:
    g.log_operator = email


def install_access_log(app: Flask) -> None:
    # Workers Logs parses JSON lines written to stdout into structured, queryable fields.
    @app.before_request
    def start_timer() -> None:
        g.started_at = time.perf_counter()

    @app.after_request
    def write_access_log(response: Response) -> Response:
        body = response.get_json(silent=True) if response.status_code >= 400 else None
        error = body.get("error") if isinstance(body, dict) else None
        record = {
            "event": "request",
            "method": request.method,
            "route": request.url_rule.rule if request.url_rule else None,
            "status": response.status_code,
            "error_code": error.get("code") if isinstance(error, dict) else None,
            "key_fingerprint": g.get("key_fingerprint"),
            "duration_ms": round((time.perf_counter() - g.get("started_at", time.perf_counter())) * 1000, 1),
        }
        if "log_operator" in g:
            record["operator"] = g.log_operator
        print(json.dumps(record), flush=True)
        return response
