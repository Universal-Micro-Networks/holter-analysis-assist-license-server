import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI is not available")


@pytest.fixture(scope="module")
def compose() -> dict:
    result = subprocess.run(
        ["docker", "compose", "--profile", "test", "config", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def volume_targets(service: dict) -> dict[str, str]:
    return {volume["target"]: volume["type"] for volume in service["volumes"]}


def test_app_service_publishes_dev_server_port(compose: dict) -> None:
    [port] = compose["services"]["app"]["ports"]
    assert (port["published"], port["target"]) == ("8787", 8787)


def test_app_service_healthcheck_uses_healthz(compose: dict) -> None:
    assert "http://localhost:8787/healthz" in " ".join(compose["services"]["app"]["healthcheck"]["test"])


def test_app_service_runs_entrypoint_script(compose: dict) -> None:
    assert compose["services"]["app"]["command"] == ["docker/dev-entrypoint.sh"]


def test_app_keeps_generated_files_out_of_the_host(compose: dict) -> None:
    targets = volume_targets(compose["services"]["app"])
    assert targets["/app"] == "bind"
    for generated in ["/app/.wrangler", "/app/.venv-workers", "/app/node_modules"]:
        assert targets[generated] == "volume", generated


def test_python_modules_is_not_a_mount_point(compose: dict) -> None:
    # pywrangler re-syncs with rmtree(python_modules), which fails with EBUSY on a mount point.
    assert "/app/python_modules" not in volume_targets(compose["services"]["app"])


def test_test_service_only_starts_on_demand(compose: dict) -> None:
    test = compose["services"]["test"]
    assert test["profiles"] == ["test"]
    assert test["command"] == ["uv", "run", "pytest"]


def test_dockerignore_excludes_generated_and_secret_files() -> None:
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").split())
    assert {".venv", ".venv-workers", "python_modules", "node_modules", ".wrangler", ".dev.vars", "pylock.toml"} <= ignored


def test_entrypoint_script_is_executable() -> None:
    assert os.access(ROOT / "docker" / "dev-entrypoint.sh", os.X_OK)
