"""`make ship-api` / `make ship-gpu` (add-on 4.1), run offline against fakes.

The API pod is a temporary "workspace" directory: a fake `uv` builds the venv, a fake restart
script records which release came up, and a loopback HTTP server stands in for GET /health and
reports that release's REVISION (503 for a release marked BROKEN). `ssh` runs its command in a
local shell; `aws` only records its argv. Nothing leaves the machine.
"""

# ruff: noqa: S603 - our own scripts and git, fixed arguments

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REMOTE = ROOT / "scripts" / "deploy" / "ship_api_remote.sh"
SHIP_API = ROOT / "scripts" / "deploy" / "ship_api.sh"
SHIP_GPU = ROOT / "scripts" / "deploy" / "ship_gpu.sh"
BASH = shutil.which("bash")
GIT = shutil.which("git")
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40

pytestmark = pytest.mark.skipif(BASH is None or GIT is None, reason="needs bash and git")

FAKE_SSH = """#!/bin/sh
# Records its options, then runs the remote command in a local shell.
printf '%s\\n' "$*" >> "$SSH_LOG"
while [ $# -gt 0 ]; do
  case $1 in
    -i | -p | -o) shift 2 ;;
    *@*) shift; break ;;
    *) shift ;;
  esac
done
exec bash -c "$1"
"""
FAKE_UV = """#!/bin/sh
[ -z "$FAKE_UV_FAIL" ] || exit 1
mkdir -p "$UV_PROJECT_ENVIRONMENT"
"""
FAKE_SETSID = '#!/bin/sh\nexec "$@"\n'
# The restart script on the pod: "brings up" whatever release the live symlink points at.
FAKE_RESTART = """#!/bin/sh
if [ -e "$LIVE/BROKEN" ]; then echo down > "$STATE"
elif [ -f "$LIVE/REVISION" ]; then cat "$LIVE/REVISION" > "$STATE"
else echo none > "$STATE"; fi
"""
FAKE_AWS = """#!/bin/sh
python3 -c 'import json, os, sys
with open(os.environ["AWS_LOG"], "a") as out:
    out.write(json.dumps({"argv": sys.argv[1:], "key": os.environ.get("AWS_ACCESS_KEY_ID"),
                          "secret_set": bool(os.environ.get("AWS_SECRET_ACCESS_KEY"))}) + "\\n")
' "$@"
"""


class Health(ThreadingHTTPServer):
    """GET /health as the API answers it, driven by the state the fake restart wrote."""

    state: Path


class HealthHandler(BaseHTTPRequestHandler):
    server: Health

    def do_GET(self) -> None:
        state = self.server.state.read_text().strip() if self.server.state.exists() else "down"
        if state == "down":
            self.send_response(503)
            self.end_headers()
            return
        body = json.dumps({"ok": True, "revision": None if state == "none" else state}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def _tool(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text(body)
    path.chmod(0o755)


class Pod:
    """A fake API pod: /workspace with the hand-made code directory and the restart script."""

    def __init__(self, tmp_path: Path, health_url: str) -> None:
        self.ws = (tmp_path / "workspace").resolve()
        self.live = self.ws / "rulecam"
        self.shared = self.ws / "rulecam-shared"
        self.releases = self.ws / "releases"
        self.state = tmp_path / "health-state"
        self.bin = tmp_path / "bin"
        for name, body in {"uv": FAKE_UV, "ssh": FAKE_SSH, "setsid": FAKE_SETSID}.items():
            _tool(self.bin, name, body)
        _tool(self.ws, "restart_api.sh", FAKE_RESTART)
        (self.live / "data").mkdir(parents=True)
        (self.live / "data" / "tracks.parquet").write_text("rows")
        (self.live / "plans").mkdir()
        (self.live / "plans" / "owner.yaml").write_text("owner plan")
        (self.live / "src").mkdir()
        (self.live / "src" / "old.py").write_text("old code")
        for secret in (".env", ".sam_edge_key", ".glimmer_edge_key", ".glimmer_api_key"):
            (self.live / secret).write_text(f"{secret} value")
        (self.live / "start.sh").write_text("#!/bin/sh\n")
        self.env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "RELEASES": str(self.releases),
            "LIVE": str(self.live),
            "SHARED": str(self.shared),
            "RESTART": str(self.ws / "restart_api.sh"),
            "HEALTH_URL": health_url,
            "HEALTH_WAIT_S": "3",
            "HEALTH_SETTLE_S": "0",
            "HEALTH_POLL_S": "0.1",
            "STATE": str(self.state),
            "SSH_LOG": str(tmp_path / "ssh.log"),
            "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        }

    def release(self, sha: str, *, broken: bool = False) -> Path:
        """Unpack a release as ship_api.sh leaves it: code, a tracked plans/ and REVISION."""
        rel = self.releases / sha[:12]
        (rel / "src").mkdir(parents=True)
        (rel / "src" / "new.py").write_text(f"code {sha[:4]}")
        (rel / "plans").mkdir()
        (rel / "plans" / "committed.yaml").write_text("committed plan")
        (rel / "REVISION").write_text(f"{sha}\n")
        if broken:
            (rel / "BROKEN").write_text("")
        return rel

    def ship(self, sha: str, **env: str) -> subprocess.CompletedProcess[str]:
        """Run the remote half in the foreground, as the detached run on the pod does."""
        return subprocess.run(
            [str(BASH), str(REMOTE), sha],
            env={**self.env, **env},
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def status(self, sha: str) -> str:
        return (self.releases / f"ship-{sha[:12]}.status").read_text().strip()

    def live_target(self) -> Path:
        return self.live.resolve()


@pytest.fixture
def pod(tmp_path: Path) -> Iterator[Pod]:
    server = Health(("127.0.0.1", 0), HealthHandler)
    server.state = tmp_path / "health-state"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Pod(tmp_path, f"http://127.0.0.1:{server.server_address[1]}/health")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_first_ship_moves_the_hand_made_code_aside_and_goes_live(pod: Pod) -> None:
    rel = pod.release(SHA_A)
    result = pod.ship(SHA_A)
    assert result.returncode == 0, result.stdout + result.stderr
    assert pod.status(SHA_A) == "ok"
    assert pod.live.is_symlink()
    assert pod.live_target() == rel
    assert pod.state.read_text().strip() == SHA_A  # /health reported the new revision
    # The secrets and start.sh survive, shared through links; no copy of a key in the release.
    for secret in (".env", ".sam_edge_key", ".glimmer_edge_key", ".glimmer_api_key", "start.sh"):
        assert (rel / secret).is_symlink()
        assert (rel / secret).resolve() == pod.shared / secret
    assert (pod.live / ".env").read_text() == ".env value"
    # The API's own data and the owner's plans are shared; the release's code is its own.
    assert (rel / "data").resolve() == pod.shared / "data"
    assert (pod.live / "plans" / "owner.yaml").read_text() == "owner plan"
    assert (pod.live / "src" / "new.py").read_text() == "code aaaa"
    assert not (pod.live / "src" / "old.py").exists()
    assert (rel / ".venv").is_dir()


def test_a_release_that_never_reports_its_revision_is_rolled_back(pod: Pod) -> None:
    first = pod.release(SHA_A)
    assert pod.ship(SHA_A).returncode == 0
    pod.release(SHA_B, broken=True)
    result = pod.ship(SHA_B)
    assert result.returncode == 1
    assert pod.status(SHA_B) == "rolled-back"
    assert pod.live_target() == first
    assert pod.state.read_text().strip() == SHA_A
    assert "rolling back" in result.stdout


def test_a_failed_venv_build_never_touches_the_live_api(pod: Pod) -> None:
    pod.release(SHA_A)
    result = pod.ship(SHA_A, FAKE_UV_FAIL="1")
    assert result.returncode != 0
    assert pod.status(SHA_A) == "failed"
    assert pod.live_target() == pod.shared  # still the old code (moved aside, same files)
    assert (pod.live / "src" / "old.py").exists()
    assert not pod.state.exists()  # the API was never restarted


def test_reshipping_the_live_release_is_a_no_op(pod: Pod) -> None:
    pod.release(SHA_A)
    assert pod.ship(SHA_A).returncode == 0
    pod.state.unlink()
    result = pod.ship(SHA_A)
    assert result.returncode == 0
    assert "already live" in result.stdout
    assert not pod.state.exists()


def test_old_releases_are_pruned_but_never_the_live_or_previous_one(pod: Pod) -> None:
    for sha in (SHA_A, SHA_B, SHA_C):
        pod.release(sha)
        assert pod.ship(sha, KEEP="1").returncode == 0
    names = sorted(p.name for p in pod.releases.iterdir() if p.is_dir() and len(p.name) == 12)
    assert names == [SHA_B[:12], SHA_C[:12]]


def test_missing_release_or_restart_script_fails_cleanly(pod: Pod) -> None:
    result = pod.ship(SHA_A)  # nothing uploaded
    assert result.returncode == 1
    assert "has no REVISION" in result.stdout
    assert pod.status(SHA_A) == "failed"
    assert not pod.live.is_symlink()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [str(GIT), "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small checkout holding the ship scripts, some code and the GPU start scripts."""
    repo = tmp_path / "repo"
    deploy = repo / "scripts" / "deploy"
    deploy.mkdir(parents=True)
    shutil.copy2(REMOTE, deploy / REMOTE.name)
    (repo / "src" / "rulecam").mkdir(parents=True)
    (repo / "src" / "rulecam" / "__init__.py").write_text("")
    (repo / "scripts" / "sam_pod").mkdir()
    (repo / "scripts" / "sam_pod" / "serverless.sh").write_text("#!/bin/sh\n")
    (repo / "scripts" / "glimmer_pod").mkdir()
    (repo / "scripts" / "glimmer_pod" / "serverless.sh").write_text("#!/bin/sh\n")
    (repo / "plans").mkdir()
    (repo / "plans" / "committed.yaml").write_text("committed plan")
    (repo / "README.md").write_text("not shipped to the GPUs")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


def _run(
    script: Path, repo: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BASH), str(script), *args],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_ship_api_uploads_switches_and_waits_for_the_revision(pod: Pod, repo: Path) -> None:
    key = pod.ws.parent / "ssh-key"
    key.write_text("not a key")
    env = {
        **pod.env,
        "RULECAM_API_SSH_HOST": "203.0.113.7",
        "RULECAM_API_SSH_PORT": "40022",
        "RULECAM_API_SSH_KEY": str(key),
        "POLL_S": "0.2",
        "SHIP_TIMEOUT_S": "60",
    }
    result = _run(SHIP_API, repo, env)
    sha = _git(repo, "rev-parse", "HEAD")
    assert result.returncode == 0, result.stderr
    assert f"live: {sha}" in result.stderr
    rel = pod.releases / sha[:12]
    assert pod.live_target() == rel
    assert (rel / "REVISION").read_text().strip() == sha
    assert (rel / "plans").resolve() == pod.shared / "plans"  # the owner's plans win
    assert not list(pod.releases.glob(".incoming-*"))
    ssh_calls = Path(env["SSH_LOG"]).read_text()
    assert "ServerAliveInterval=15" in ssh_calls
    assert "-p 40022" in ssh_calls
    assert "root@203.0.113.7" in ssh_calls


def test_ship_api_needs_the_ssh_target(repo: Path, tmp_path: Path) -> None:
    result = _run(SHIP_API, repo, {"PATH": os.environ["PATH"], "HOME": str(tmp_path)})
    assert result.returncode == 2
    assert "RULECAM_API_SSH_HOST" in result.stderr


def _gpu_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    _tool(bin_dir, "aws", FAKE_AWS)
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "AWS_LOG": str(tmp_path / "aws.log"),
        **extra,
    }


def test_ship_gpu_copies_the_committed_code_to_the_volume(repo: Path, tmp_path: Path) -> None:
    secret = "rps_test_not_a_real_secret"  # noqa: S105
    env = _gpu_env(
        tmp_path, RUNPOD_S3_ACCESS_KEY_ID="user_test", RUNPOD_S3_SECRET_ACCESS_KEY=secret
    )
    (repo / "src" / "rulecam" / "uncommitted.py").write_text("")
    result = _run(SHIP_GPU, repo, env, "HEAD")
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in (tmp_path / "aws.log").read_text().splitlines()]
    argvs = [call["argv"] for call in calls]
    assert all(call["key"] == "user_test" and call["secret_set"] for call in calls)
    assert all(secret not in " ".join(argv) for argv in argvs)
    for argv in argvs:
        assert argv[0] == "s3"
        assert argv[argv.index("--region") + 1] == "us-il-1"
        assert argv[argv.index("--endpoint-url") + 1] == "https://s3api-us-il-1.runpod.io"
    dests = [argv[3] for argv in argvs]
    assert "s3://11ymk65bni/rulecam/src" in dests
    assert "s3://11ymk65bni/rulecam/scripts/sam_pod" in dests
    assert dests[-1] == "s3://11ymk65bni/rulecam/REVISION"
    syncs = [argv for argv in argvs if argv[1] == "sync"]
    assert syncs
    assert all("--delete" in argv for argv in syncs)
    assert all("--dryrun" not in argv for argv in argvs)


def test_ship_gpu_dry_run_and_other_volume(repo: Path, tmp_path: Path) -> None:
    env = _gpu_env(
        tmp_path,
        AWS_ACCESS_KEY_ID="id",
        AWS_SECRET_ACCESS_KEY="secret",  # noqa: S106
        RUNPOD_VOLUME_ID="vol123",
        RUNPOD_S3_REGION="eu-ro-1",
        SHIP_GPU_PATHS="src",
        DRY_RUN="1",
    )
    result = _run(SHIP_GPU, repo, env)
    assert result.returncode == 0, result.stderr
    argvs = [json.loads(line)["argv"] for line in (tmp_path / "aws.log").read_text().splitlines()]
    assert all("--dryrun" in argv for argv in argvs)
    assert {argv[3] for argv in argvs} == {
        "s3://vol123/rulecam/src",
        "s3://vol123/rulecam/REVISION",
    }
    assert all("https://s3api-eu-ro-1.runpod.io" in argv for argv in argvs)


def test_ship_gpu_needs_s3_credentials(repo: Path, tmp_path: Path) -> None:
    result = _run(SHIP_GPU, repo, _gpu_env(tmp_path))
    assert result.returncode == 2
    assert "RUNPOD_S3_ACCESS_KEY_ID" in result.stderr
    assert not (tmp_path / "aws.log").exists()
