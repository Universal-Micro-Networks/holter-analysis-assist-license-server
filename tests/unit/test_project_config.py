import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_jsonc(path: Path) -> dict:
    # Only full-line `//` comments are allowed in wrangler.jsonc so that URLs inside strings stay intact.
    text = re.sub(r"^\s*//.*$", "", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    return json.loads(text)


@pytest.fixture(scope="module")
def wrangler() -> dict:
    return load_jsonc(ROOT / "wrangler.jsonc")


def test_wrangler_runs_python_worker_entrypoint(wrangler: dict) -> None:
    assert wrangler["main"] == "src/worker.py"
    assert "python_workers" in wrangler["compatibility_flags"]


def test_wrangler_binds_d1_as_db_with_migrations(wrangler: dict) -> None:
    [d1] = wrangler["d1_databases"]
    assert d1["binding"] == "DB"
    assert d1["migrations_dir"] == "migrations"


def test_wrangler_binds_rate_limiter_120_per_minute(wrangler: dict) -> None:
    [limiter] = wrangler["ratelimits"]
    assert limiter["name"] == "RATE_LIMITER"
    assert limiter["simple"] == {"limit": 120, "period": 60}


def test_wrangler_enables_workers_logs(wrangler: dict) -> None:
    assert wrangler["observability"]["enabled"] is True


def test_wrangler_disables_public_workers_dev_urls(wrangler: dict) -> None:
    assert wrangler["workers_dev"] is False
    assert wrangler["preview_urls"] is False


def is_git_ignored(relative_path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", relative_path],
        cwd=ROOT,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    "path",
    [".venv/x", ".venv-workers/x", "python_modules/x", "pylock.toml", ".wrangler/x", "node_modules/x", ".dev.vars"],
)
def test_generated_files_and_local_secrets_are_ignored(path: str) -> None:
    assert is_git_ignored(path)


@pytest.mark.parametrize("path", ["uv.lock", "package-lock.json", ".dev.vars.example"])
def test_lock_files_and_secret_template_are_tracked(path: str) -> None:
    assert not is_git_ignored(path)


def test_secret_template_declares_admin_token() -> None:
    template = (ROOT / ".dev.vars.example").read_text(encoding="utf-8")
    assert re.search(r"^ADMIN_API_TOKEN=", template, flags=re.MULTILINE)
