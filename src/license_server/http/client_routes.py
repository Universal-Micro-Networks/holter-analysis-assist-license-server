from flask import Blueprint, Response

from license_server.http.dependencies import guard_client
from license_server.http.dto import usage_summary_dto
from license_server.http.responses import success_response

client_api = Blueprint("client_api", __name__, url_prefix="/v1")


@client_api.post("/licenses/verify")
def verify_license() -> Response:
    deps, key = guard_client()
    license_ = deps.licenses.require_active(key)
    return success_response({"valid": True, "monthly_limit": license_.monthly_limit, "status": str(license_.status)})


@client_api.post("/usage")
def record_usage() -> Response:
    # The body is deliberately never read: no inference input or output is stored.
    deps, key = guard_client()
    summary = deps.usage.record_usage(key)
    return success_response({"allowed": True, **usage_summary_dto(summary)}, status=201)


@client_api.get("/usage/current")
def current_usage() -> Response:
    deps, key = guard_client()
    return success_response(usage_summary_dto(deps.usage.current_summary(key)))
