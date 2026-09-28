#!/bin/sh
set -eu
cd /app

if [ ! -f .dev.vars ]; then
    cp .dev.vars.example .dev.vars
    echo "[entrypoint] Created .dev.vars from .dev.vars.example (local secrets)."
fi

uv sync

lock_hash=$(sha256sum package-lock.json | cut -d' ' -f1)
if [ "$(cat node_modules/.lock-hash 2>/dev/null || true)" != "$lock_hash" ]; then
    echo "[entrypoint] package-lock.json changed; reinstalling node modules."
    npm ci --no-audit --no-fund
    echo "$lock_hash" > node_modules/.lock-hash
fi

uv run pywrangler d1 migrations apply DB --local

exec uv run pywrangler dev --ip 0.0.0.0 --port 8787
