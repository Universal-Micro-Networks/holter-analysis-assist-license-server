"""Check that the Worker can fetch Cloudflare Access signing keys with the runtime `fetch`.

Starts a second, temporary `wrangler dev` inside the app container (port 8788, separate state directory)
with Access pointed at a public team domain, then sends a token that names one of that team's real key
ids but carries a signature from the test key. If the keys were fetched and parsed, the Worker rejects the
signature (403 and "bad_signature" in its log); if fetching failed it answers 503.

Usage: docker compose up -d app && uv run python scripts/console_access_fetch_check.py
"""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.fakes.access import make_token  # noqa: E402

TEAM_DOMAIN = "https://cloudflare.cloudflareaccess.com"
PORT = 8788
LOG = "/tmp/access-fetch-check.log"


def container(command: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "app", "sh", "-c", command],
        capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False,
    )


def main() -> None:
    with urllib.request.urlopen(f"{TEAM_DOMAIN}/cdn-cgi/access/certs", timeout=10) as response:
        kid = json.load(response)["keys"][0]["kid"]
    token = make_token(header={"kid": kid}, iss=TEAM_DOMAIN, aud=["runtime-check"])

    container(
        f"cd /app && nohup npx wrangler dev --port {PORT} --inspector-port 9239 --persist-to /tmp/access-check "
        f"--var ACCESS_TEAM_DOMAIN:{TEAM_DOMAIN} --var ACCESS_AUD:runtime-check > {LOG} 2>&1 &"
    )
    try:
        for _ in range(60):
            if container(f"curl -fsS http://localhost:{PORT}/healthz").returncode == 0:
                break
            time.sleep(2)
        else:
            print(container(f"tail -40 {LOG}").stdout, file=sys.stderr)
            sys.exit("temporary dev server did not start")
        status = container(
            f"curl -s -o /dev/null -w '%{{http_code}}' -H 'Cf-Access-Jwt-Assertion: {token}' "
            f"http://localhost:{PORT}/console/licenses"
        ).stdout.strip()
        time.sleep(1)
        log = container(f"cat {LOG}").stdout
    finally:
        container(
            "for d in /proc/[0-9]*; do c=$(tr '\\0' ' ' < $d/cmdline 2>/dev/null); "
            f"case \"$c\" in *'--port {PORT}'*|*'--inspector-port 9239'*) kill ${{d#/proc/}} 2>/dev/null;; esac; done"
        )

    print(f"status={status}")
    if status == "403" and "Access token rejected: bad_signature" in log:
        print("ok   the Worker fetched the Access signing keys and verified the signature against them")
        print("access fetch check passed")
        return
    if "Access certs unavailable" in log:
        sys.exit("FAIL the Worker could not fetch the Access signing keys")
    print(log[-3000:], file=sys.stderr)
    sys.exit("FAIL unexpected result")


if __name__ == "__main__":
    main()
