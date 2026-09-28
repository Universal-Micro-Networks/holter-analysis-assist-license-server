import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

WORKERS_ENV_KEY = "workers.env"


@dataclass(frozen=True)
class WorkerBindings:
    db: Any
    rate_limiter: Any | None
    admin_token: str | None


@dataclass(frozen=True)
class ConsoleSettings:
    access_team_domain: str | None  # "https://<team>.cloudflareaccess.com"
    access_audience: str | None
    session_secret: str | None
    # Local development only; honored solely when Access is not configured and the host is loopback.
    dev_operator_email: str | None

    @property
    def access_configured(self) -> bool:
        return self.access_team_domain is not None and self.access_audience is not None


_HOST_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")


def _workers_env(environ: Mapping[str, Any]) -> Any:
    env = environ.get(WORKERS_ENV_KEY)
    if env is None:
        raise RuntimeError("workers.env is not available; is this running on Cloudflare Workers?")
    return env


def _text(env: Any, name: str) -> str | None:
    value = getattr(env, name, None)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _team_domain(value: str | None) -> str | None:
    if value is None:
        return None
    host = value.removeprefix("https://").rstrip("/")
    if "://" in value and not value.startswith("https://"):
        return None
    return f"https://{host}" if _HOST_PATTERN.fullmatch(host) else None


def console_settings_from_environ(environ: Mapping[str, Any]) -> ConsoleSettings:
    env = _workers_env(environ)
    return ConsoleSettings(
        access_team_domain=_team_domain(_text(env, "ACCESS_TEAM_DOMAIN")),
        access_audience=_text(env, "ACCESS_AUD"),
        session_secret=_text(env, "CONSOLE_SESSION_SECRET"),
        dev_operator_email=_text(env, "CONSOLE_DEV_OPERATOR_EMAIL"),
    )


def bindings_from_environ(environ: Mapping[str, Any]) -> WorkerBindings:
    env = _workers_env(environ)
    db = getattr(env, "DB", None)
    if db is None:
        raise RuntimeError("D1 binding 'DB' is not configured")
    token = getattr(env, "ADMIN_API_TOKEN", None)
    return WorkerBindings(
        db=db,
        rate_limiter=getattr(env, "RATE_LIMITER", None),
        admin_token=token if isinstance(token, str) and token else None,
    )
