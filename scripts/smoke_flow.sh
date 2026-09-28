#!/usr/bin/env bash
# End-to-end check against a running dev server (`docker compose up`):
# issue -> verify -> record -> current -> suspend -> rejected -> activate -> record -> limit reached -> logs.
# Response bodies are printed with license keys redacted.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8787}"
ADMIN_API_TOKEN="${ADMIN_API_TOKEN:-$(sed -n 's/^ADMIN_API_TOKEN=//p' .dev.vars)}"

BODY=$(mktemp)
trap 'rm -f "$BODY"' EXIT

redacted_body() {
  sed -E 's/lk_[0-9a-f]{32}/lk_<redacted>/g' "$BODY"
}

step() {
  local name="$1" expected="$2"
  shift 2
  local status
  status=$(curl -sS -o "$BODY" -w '%{http_code}' "$@")
  if [[ "$status" != "$expected" ]]; then
    echo "FAIL $name: expected $expected, got $status: $(redacted_body)" >&2
    exit 1
  fi
  echo "ok   $name ($status) $(redacted_body)"
}

admin() {
  curl_args=(-X POST "$BASE_URL/v1/admin/$1" -H "Authorization: Bearer $ADMIN_API_TOKEN" -H 'Content-Type: application/json' -d "$2")
}

admin licenses '{"monthly_limit": 2}'
step "issue license" 201 "${curl_args[@]}"
KEY=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["data"]["license_key"])' "$BODY")
CLIENT=(-H "Authorization: Bearer $KEY")

step "verify" 200 -X POST "$BASE_URL/v1/licenses/verify" "${CLIENT[@]}"
step "record usage" 201 -X POST "$BASE_URL/v1/usage" "${CLIENT[@]}"
step "current usage" 200 "$BASE_URL/v1/usage/current" "${CLIENT[@]}"
admin licenses/suspend "{\"license_key\": \"$KEY\"}"
step "suspend" 200 "${curl_args[@]}"
step "record while suspended" 403 -X POST "$BASE_URL/v1/usage" "${CLIENT[@]}"
admin licenses/activate "{\"license_key\": \"$KEY\"}"
step "activate" 200 "${curl_args[@]}"
step "record after activate" 201 -X POST "$BASE_URL/v1/usage" "${CLIENT[@]}"
step "limit reached" 403 -X POST "$BASE_URL/v1/usage" "${CLIENT[@]}"
admin usage/logs "{\"license_key\": \"$KEY\", \"from\": \"2000-01-01T00:00:00Z\", \"to\": \"2100-01-01T00:00:00Z\"}"
step "usage logs" 200 "${curl_args[@]}"
echo "smoke flow passed"
