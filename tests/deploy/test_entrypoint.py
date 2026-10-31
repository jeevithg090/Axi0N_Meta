"""Container entrypoints (WS5): secret loading, documented uvicorn flags, SAM start-up."""

# ruff: noqa: S104, S105, S106 - fake keys; 0.0.0.0 is the flag under test

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_ENTRYPOINT = ROOT / "deploy" / "api-entrypoint.sh"
SAM_ENTRYPOINT = ROOT / "deploy" / "sam-entrypoint.sh"
BASH = shutil.which("bash")
SECRET = "sk-test-not-a-real-key-1234"

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

# A stand-in for uvicorn / python: records its argv, working directory and the environment
# variables the entrypoint is responsible for, as JSON.
RECORDER = """#!/usr/bin/env python3
import json, os, sys
names = os.environ.get("RECORD_NAMES", "").split(",")
if sys.argv[1:2] == ["-"]:  # an inline script (the SAM checkpoint check / download)
    sys.stdin.read()
    sys.exit(int(os.environ.get("FAKE_PY_EXIT", "0")))
with open(os.environ["RECORD_TO"], "w") as out:
    json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(),
               "env": {n: os.environ.get(n) for n in names}}, out)
"""


def write_recorder(bin_dir: Path, name: str) -> Path:
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text(RECORDER.replace("#!/usr/bin/env python3", f"#!{shutil.which('python3')}"))
    path.chmod(0o755)
    return path


def run(
    script: Path, *args: str, env: dict[str, str], tmp_path: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
    record = tmp_path / "record.json"
    record.unlink(missing_ok=True)
    full_env = {
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "RECORD_TO": str(record),
        "RECORD_NAMES": ",".join(
            [
                "META_API_KEY",
                "GLIMMER_API_KEY",
                "SAM_API_KEY",
                "HF_TOKEN",
                "HF_HUB_OFFLINE",
                "HF_HOME",
                "PUBLIC_TUNNEL_URL",
                "SAM_DET_THRESH",
                "SESSION_SECRET",
            ]
        ),
        **env,
    }
    result = subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [str(BASH), str(script), *args],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    data = json.loads(record.read_text()) if record.exists() else None
    return result, data


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    write_recorder(tmp_path / "bin", "uvicorn")
    return path


def secret_file(path: Path, value: str, mode: int = 0o600) -> None:
    path.write_text(value + "\n")
    path.chmod(mode)


def test_serve_runs_uvicorn_with_the_documented_flags(home: Path, tmp_path: Path) -> None:
    secret_file(home / ".meta_api_key", SECRET)
    secret_file(home / ".session_secret", "s" * 64)
    result, record = run(API_ENTRYPOINT, env={"RULECAM_HOME": str(home)}, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    assert record is not None
    assert record["argv"] == [
        "rulecam.api.app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--timeout-graceful-shutdown",
        "5",
        "--forwarded-allow-ips",
        "100.64.0.0/10",
    ]
    assert Path(str(record["cwd"])).resolve() == home.resolve()
    env = record["env"]
    assert isinstance(env, dict)
    assert env["META_API_KEY"] == SECRET  # trailing newline stripped
    assert env["GLIMMER_API_KEY"] is None
    assert env["SESSION_SECRET"] == "s" * 64  # owner login signing secret
    assert "secret META_API_KEY from" in result.stderr
    assert SECRET not in result.stderr + result.stdout


def test_environment_wins_over_the_file_and_placeholders_fall_back(
    home: Path, tmp_path: Path
) -> None:
    secret_file(home / ".meta_api_key", "from-file")
    secret_file(home / ".glimmer_api_key", "glimmer-from-file")
    result, record = run(
        API_ENTRYPOINT,
        env={
            "RULECAM_HOME": str(home),
            "META_API_KEY": SECRET,
            "GLIMMER_API_KEY": "{{ RUNPOD_SECRET_rulecam_glimmer_api_key }}",
        },
        tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert record is not None
    env = record["env"]
    assert isinstance(env, dict)
    assert env["META_API_KEY"] == SECRET
    assert env["GLIMMER_API_KEY"] == "glimmer-from-file"
    assert "unresolved RunPod secret reference" in result.stderr
    assert SECRET not in result.stderr
    assert "glimmer-from-file" not in result.stderr


def test_secret_file_mode_is_tightened_and_empty_files_skipped(home: Path, tmp_path: Path) -> None:
    secret_file(home / ".sam_api_key", "sam-key", mode=0o644)
    (home / ".hf_token").write_text("")
    result, record = run(API_ENTRYPOINT, env={"RULECAM_HOME": str(home)}, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (home / ".sam_api_key").stat().st_mode & 0o777 == 0o600
    assert record is not None
    env = record["env"]
    assert isinstance(env, dict)
    assert env["SAM_API_KEY"] == "sam-key"
    assert env["HF_TOKEN"] is None
    assert ".hf_token is empty" in result.stderr


def test_public_tunnel_url_defaults_to_the_runpod_proxy(home: Path, tmp_path: Path) -> None:
    _, record = run(
        API_ENTRYPOINT,
        env={"RULECAM_HOME": str(home), "RUNPOD_POD_ID": "abc123"},
        tmp_path=tmp_path,
    )
    assert record is not None
    env = record["env"]
    assert isinstance(env, dict)
    assert env["PUBLIC_TUNNEL_URL"] == "https://abc123-8000.proxy.runpod.net"


def test_trusting_every_forwarder_is_refused(home: Path, tmp_path: Path) -> None:
    result, record = run(
        API_ENTRYPOINT,
        env={"RULECAM_HOME": str(home), "RULECAM_FORWARDED_ALLOW_IPS": "*"},
        tmp_path=tmp_path,
    )
    assert result.returncode == 1
    assert record is None
    assert "must not trust every forwarder" in result.stderr


def test_other_commands_get_the_same_secrets(home: Path, tmp_path: Path) -> None:
    secret_file(home / ".glimmer_api_key", SECRET)
    write_recorder(tmp_path / "bin", "preflight")
    result, record = run(
        API_ENTRYPOINT, "preflight", "--x", env={"RULECAM_HOME": str(home)}, tmp_path=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert record is not None
    assert record["argv"] == ["--x"]
    env = record["env"]
    assert isinstance(env, dict)
    assert env["GLIMMER_API_KEY"] == SECRET


def test_healthcheck_fails_when_nothing_listens(tmp_path: Path) -> None:
    result, _ = run(API_ENTRYPOINT, "healthcheck", env={"RULECAM_PORT": "9"}, tmp_path=tmp_path)
    assert result.returncode == 1


@pytest.fixture
def sam_home(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    write_recorder(tmp_path / "bin", "fakepython")
    return path


def sam_env(sam_home: Path, tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"SAM_HOME": str(sam_home), "SAM_PYTHON": str(tmp_path / "bin" / "fakepython"), **extra}


def test_sam_serves_offline_with_the_key_and_without_the_token(
    sam_home: Path, tmp_path: Path
) -> None:
    secret_file(sam_home / ".sam_api_key", SECRET)
    (sam_home / "sam.env").write_text("SAM_DET_THRESH=0.3\n")
    result, record = run(
        SAM_ENTRYPOINT,
        env=sam_env(sam_home, tmp_path, HF_TOKEN="hf-secret"),
        tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert record is not None
    assert record["argv"] == [
        "-m",
        "rulecam.perception.sam_server",
        "--host",
        "0.0.0.0",
        "--port",
        "8090",
    ]
    env = record["env"]
    assert isinstance(env, dict)
    assert env["SAM_API_KEY"] == SECRET
    assert env["HF_TOKEN"] is None  # the server never sees the Hugging Face token
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["HF_HOME"] == str(sam_home / "hf-cache")
    assert env["SAM_DET_THRESH"] == "0.3"
    assert "checkpoint cached" in result.stderr
    assert SECRET not in result.stderr
    assert "hf-secret" not in result.stderr


def test_sam_needs_its_key(sam_home: Path, tmp_path: Path) -> None:
    result, record = run(SAM_ENTRYPOINT, env=sam_env(sam_home, tmp_path), tmp_path=tmp_path)
    assert result.returncode == 1
    assert record is None
    assert "no SAM_API_KEY" in result.stderr


def test_sam_uncached_checkpoint_needs_a_token(sam_home: Path, tmp_path: Path) -> None:
    result, record = run(
        SAM_ENTRYPOINT,
        env=sam_env(sam_home, tmp_path, SAM_API_KEY=SECRET, FAKE_PY_EXIT="1"),
        tmp_path=tmp_path,
    )
    assert result.returncode == 1
    assert record is None
    assert "no Hugging Face token" in result.stderr


def test_sam_download_failure_names_the_gated_model(sam_home: Path, tmp_path: Path) -> None:
    secret_file(sam_home / ".hf_token", "hf-secret")
    result, _ = run(
        SAM_ENTRYPOINT,
        env=sam_env(sam_home, tmp_path, SAM_API_KEY=SECRET, FAKE_PY_EXIT="1"),
        tmp_path=tmp_path,
    )
    assert result.returncode == 1
    assert "access to facebook/sam3.1" in result.stderr
    assert "hf-secret" not in result.stderr


def test_sam_rejects_unknown_commands(tmp_path: Path) -> None:
    result, _ = run(SAM_ENTRYPOINT, "bogus", env={}, tmp_path=tmp_path)
    assert result.returncode == 1
    assert "unknown command" in result.stderr
