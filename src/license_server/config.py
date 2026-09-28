from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

WORKERS_ENV_KEY = "workers.env"


@dataclass(frozen=True)
class WorkerBindings:
    db: Any
    rate_limiter: Any | None
    admin_token: str | None


def bindings_from_environ(environ: Mapping[str, Any]) -> WorkerBindings:
    env = environ.get(WORKERS_ENV_KEY)
    if env is None:
        raise RuntimeError("workers.env is not available; is this running on Cloudflare Workers?")
    db = getattr(env, "DB", None)
    if db is None:
        raise RuntimeError("D1 binding 'DB' is not configured")
    token = getattr(env, "ADMIN_API_TOKEN", None)
    return WorkerBindings(
        db=db,
        rate_limiter=getattr(env, "RATE_LIMITER", None),
        admin_token=token if isinstance(token, str) and token else None,
    )
