# Local development image only. Production runs on Cloudflare Workers.
# Debian (glibc) is required: workerd does not run on Alpine/musl.
FROM node:24-bookworm-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /app

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    WRANGLER_SEND_METRICS=false

WORKDIR /app

# pywrangler builds the Workers bundle with Python 3.14 (Pyodide); tests run on the pinned 3.13.
COPY .python-version pyproject.toml uv.lock wrangler.jsonc ./
RUN uv python install 3.13 3.14 \
    && uv sync --no-install-project \
    && uv run pywrangler sync

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund \
    && sha256sum package-lock.json | cut -d' ' -f1 > node_modules/.lock-hash

COPY . .

EXPOSE 8787
CMD ["docker/dev-entrypoint.sh"]
