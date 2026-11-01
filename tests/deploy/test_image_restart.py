"""Restart idempotency of the API image (WS5): run its entrypoint twice on one volume.

Slow and Docker-only: CI's `images` workflow builds the image and sets RULECAM_TEST_API_IMAGE;
everywhere else this skips. Each run is a fresh container on the same volume, which is what a
RunPod pod restart does (the container disk is wiped, /workspace survives).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import pytest

IMAGE = os.environ.get("RULECAM_TEST_API_IMAGE", "")
DOCKER = shutil.which("docker")
SECRET = "meta-restart-test-key-123"  # noqa: S105 - a fake key

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not IMAGE or DOCKER is None, reason="set RULECAM_TEST_API_IMAGE"),
]

# Finds uvicorn's process inside the container and prints its uid and which env names it has.
INSPECT = """
import os
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\\0")
    except OSError:
        continue
    if any(part.endswith(b"uvicorn") for part in cmd):
        env = open(f"/proc/{pid}/environ", "rb").read().split(b"\\0")
        uid = [l for l in open(f"/proc/{pid}/status") if l.startswith("Uid:")][0].split()[1]
        names = sorted(e.split(b"=", 1)[0].decode() for e in env if e)
        print(uid, *names)
        break
"""


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - fixed docker argv
        [str(DOCKER), *args], capture_output=True, text=True, check=False, timeout=300
    )
    if check and result.returncode != 0:
        pytest.fail(f"docker {args[0]} failed ({result.returncode}):\n{result.stderr}")
    return result


@pytest.fixture
def volume() -> Iterator[str]:
    name = f"rulecam-restart-{uuid.uuid4().hex[:8]}"
    docker("volume", "create", name)
    yield name
    docker("volume", "rm", "-f", name, check=False)


def wait_healthy(container: str, timeout_s: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe = docker(
            "exec", container, "/opt/rulecam/deploy/api-entrypoint.sh", "healthcheck", check=False
        )
        if probe.returncode == 0:
            return
        time.sleep(1)
    logs = docker("logs", container, check=False)
    pytest.fail(f"{container} not healthy:\n{logs.stdout}\n{logs.stderr}")


def run_once(volume: str) -> tuple[list[str], str, float]:
    container = f"rulecam-api-{uuid.uuid4().hex[:8]}"
    docker("run", "-d", "--name", container, "-v", f"{volume}:/workspace", IMAGE)
    try:
        wait_healthy(container)
        # As the service user: reading another uid's /proc/<pid>/environ needs CAP_SYS_PTRACE,
        # which Docker drops even for root.
        inspect = docker("exec", "-u", "10001", container, "python3", "-c", INSPECT).stdout.split()
        started = time.monotonic()
        docker("stop", "-t", "20", container)
        stop_s = time.monotonic() - started
        logs = docker("logs", container)
        return inspect, logs.stdout + logs.stderr, stop_s
    finally:
        docker("rm", "-f", container, check=False)


def test_entrypoint_twice_on_one_volume(volume: str) -> None:
    docker(
        "run", "--rm", "-v", f"{volume}:/workspace", "--entrypoint", "sh", IMAGE, "-c",
        f"mkdir -p /workspace/rulecam && umask 077 && printf '%s\\n' {SECRET} "
        "> /workspace/rulecam/.meta_api_key",
    )  # fmt: skip
    for _ in range(2):
        inspect, logs, stop_s = run_once(volume)
        assert inspect, f"no uvicorn process found:\n{logs}"
        uid, *names = inspect
        assert uid == "10001"  # the unprivileged rulecam user, not root
        assert "META_API_KEY" in names
        assert "secret META_API_KEY from /workspace/rulecam/.meta_api_key" in logs
        assert SECRET not in logs
        assert stop_s < 15  # --timeout-graceful-shutdown 5: a restart never hangs
    owner = docker(
        "run", "--rm", "-v", f"{volume}:/workspace", "--entrypoint", "stat", IMAGE,
        "-c", "%u %a", "/workspace/rulecam", "/workspace/rulecam/.meta_api_key",
    ).stdout.split()  # fmt: skip
    dir_uid, _, _, key_mode = owner
    assert dir_uid == "10001"  # the app's state on the volume belongs to the service user
    assert key_mode == "600"
