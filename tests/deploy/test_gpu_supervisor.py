"""`deploy/gpu_supervisor.py` (WS3/WS5): one GPU pod runs Glimmer and SAM; both restart on a
crash or a hang, each gets only its own secret. Fake processes, probes and clock; no GPU."""

# ruff: noqa: S105 - fake keys

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sv = load("gpu_supervisor", ROOT / "deploy" / "gpu_supervisor.py")
rp = load("runpod_deploy_gpu", ROOT / "scripts" / "deploy" / "runpod.py")

ENV = {
    "PATH": "/usr/bin",
    "LLAMA_API_KEY": "llama-key-123",
    "SAM_API_KEY": "sam-key-456",
    "HF_TOKEN": "hf-token-789",
    "HF_HOME": "/workspace/hf-cache",
}


class FakeProc:
    """A child that exits when told to (or on terminate, unless it ignores SIGTERM)."""

    next_pid = 100

    def __init__(self, argv: Sequence[str], env: Mapping[str, str]) -> None:
        FakeProc.next_pid += 1
        self.pid = FakeProc.next_pid
        self.argv = list(argv)
        self.env = dict(env)
        self.code: int | None = None
        self.ignores_term = False
        self.signals: list[str] = []

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.signals.append("TERM")
        if not self.ignores_term:
            self.code = -15

    def kill(self) -> None:
        self.signals.append("KILL")
        self.code = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.code is None:
            raise subprocess.TimeoutExpired("fake", timeout or 0)
        return self.code


class Harness:
    """A supervisor over fake processes, a fake clock and scripted health."""

    def __init__(self, services: Sequence[Any] | None = None, **policy: float) -> None:
        self.now = 0.0
        self.procs: list[FakeProc] = []
        self.healthy: dict[str, bool] = {}
        self.logs: list[str] = []
        self.ready = 0
        self.sup = sv.Supervisor(
            services or sv.services(ENV),
            spawn=self.spawn,
            probe=lambda url: self.healthy.get(url, False),
            clock=lambda: self.now,
            policy=sv.Policy(**policy),
            on_all_healthy=self.all_ready,
            say=self.logs.append,
        )

    def spawn(self, argv: Sequence[str], env: Mapping[str, str]) -> FakeProc:
        proc = FakeProc(argv, env)
        self.procs.append(proc)
        return proc

    def all_ready(self) -> None:
        self.ready += 1

    def at(self, now: float) -> None:
        self.now = now
        self.sup.step()

    def current(self, name: str) -> FakeProc:
        proc = self.sup.state[name].proc
        assert isinstance(proc, FakeProc)
        return proc

    def text(self) -> str:
        return "\n".join(self.logs)


def service(name: str, port: int, **extra: Any) -> Any:
    return sv.Service(
        name=name, argv=(name,), env={}, health_url=f"http://127.0.0.1:{port}/health", **extra
    )


def test_each_server_gets_only_its_own_secret() -> None:
    glimmer, sam = sv.services(ENV)
    assert glimmer.argv[0] == "/opt/llama/llama-server"
    assert glimmer.env["LLAMA_API_KEY"] == "llama-key-123"
    assert "SAM_API_KEY" not in glimmer.env
    assert "HF_TOKEN" not in glimmer.env
    assert glimmer.env["LLAMA_CACHE"] == "/workspace/llama-cache"
    assert glimmer.env["LD_LIBRARY_PATH"] == "/opt/llama:/opt/llama/cuda"
    assert glimmer.health_url == "http://127.0.0.1:8080/health"
    assert sam.argv == ("/opt/rulecam/deploy/sam-entrypoint.sh", "_serve")
    assert sam.env["SAM_API_KEY"] == "sam-key-456"
    assert "LLAMA_API_KEY" not in sam.env
    assert "HF_TOKEN" not in sam.env  # only in its start-up env, below
    assert sam.bootstrap_env == {"HF_TOKEN": "hf-token-789"}
    assert sam.env["HF_HOME"] == "/workspace/hf-cache"
    assert sam.health_url == "http://127.0.0.1:8090/health"
    for secret in ("llama-key-123", "sam-key-456", "hf-token-789"):
        assert all(secret not in arg for s in (glimmer, sam) for arg in s.argv)


def test_glimmer_args_are_the_documented_ones_and_sized_from_env() -> None:
    glimmer, _ = sv.services(ENV)
    assert list(glimmer.argv[1:]) == list(rp.GLIMMER_ARGS)  # same as the two-pod deploy
    sized = sv.glimmer_args({"GLIMMER_CTX": "16384", "GLIMMER_PARALLEL": "1", "GLIMMER_PORT": "9"})
    assert sized[sized.index("-c") + 1] == "16384"
    assert sized[sized.index("-np") + 1] == "1"
    assert sized[sized.index("--port") + 1] == "9"
    custom, _ = sv.services(
        {**ENV, "GPU_LLAMA_SERVER": "/x/llama-server", "LD_LIBRARY_PATH": "/usr/local/cuda/lib64"}
    )
    assert custom.argv[0] == "/x/llama-server"
    assert custom.env["LD_LIBRARY_PATH"] == "/opt/llama:/opt/llama/cuda:/usr/local/cuda/lib64"


def test_both_keys_are_required() -> None:
    with pytest.raises(KeyError, match="LLAMA_API_KEY"):
        sv.services({"SAM_API_KEY": "x"})
    with pytest.raises(KeyError, match="SAM_API_KEY"):
        sv.services({"LLAMA_API_KEY": "x", "SAM_API_KEY": ""})
    _, sam = sv.services({"LLAMA_API_KEY": "a", "SAM_API_KEY": "b"})
    assert sam.bootstrap_env == {}  # no token: the checkpoint must already be cached


def test_both_start_at_once_and_a_crash_restarts_with_backoff() -> None:
    h = Harness()
    h.at(0)
    assert [p.argv[0] for p in h.procs] == ["/opt/llama/llama-server", sv.SAM_ENTRYPOINT]
    first = h.current("glimmer")
    first.code = 1
    h.at(5)  # exited after 5 s: restart in 2 s
    assert h.sup.state["glimmer"].proc is None
    assert "glimmer exited with 1 after 5 s" in h.text()
    h.at(6)
    assert h.sup.state["glimmer"].proc is None
    h.at(7)
    second = h.current("glimmer")
    assert second is not first
    second.code = 1
    h.at(8)
    h.at(11)
    assert h.sup.state["glimmer"].proc is None  # 4 s now
    h.at(12)
    third = h.current("glimmer")
    third.code = 1
    h.at(13)
    assert "glimmer restarts in 8 s" in h.text()
    assert h.current("sam").code is None  # SAM is untouched by Glimmer's crashes


def test_backoff_is_capped_and_resets_after_a_healthy_run() -> None:
    h = Harness(max_backoff_s=10)
    for _ in range(6):
        h.at(h.now + 100)
        h.current("sam").code = 1
        h.at(h.now + 1)
    assert h.sup.state["sam"].crashes == 6
    assert "sam restarts in 10 s" in h.text()  # 2, 4, 8, then the 10 s cap
    h.at(h.now + 20)
    h.healthy["http://127.0.0.1:8090/health"] = True
    h.at(h.now + 1)
    assert h.sup.state["sam"].healthy
    h.current("sam").code = 1
    h.at(h.now + 1)
    assert h.sup.state["sam"].crashes == 1  # a healthy run resets the count
    assert h.logs[-1] == "sam restarts in 2 s"


def test_a_long_run_resets_the_backoff_even_without_health() -> None:
    h = Harness(stable_s=60)
    h.at(0)
    h.current("glimmer").code = 1
    h.at(1)
    h.at(3)
    h.at(100)
    h.current("glimmer").code = 1
    h.at(101)
    assert h.sup.state["glimmer"].crashes == 1


def test_the_hf_token_is_dropped_once_sam_is_healthy() -> None:
    h = Harness()
    h.at(0)
    assert h.current("sam").env["HF_TOKEN"] == "hf-token-789"
    assert "HF_TOKEN" not in h.current("glimmer").env
    h.healthy["http://127.0.0.1:8090/health"] = True
    h.at(10)
    assert "sam: start-up secrets dropped" in h.text()
    h.current("sam").code = 1
    h.at(11)
    h.at(13)
    restarted = h.current("sam")
    assert "HF_TOKEN" not in restarted.env
    assert restarted.env["SAM_API_KEY"] == "sam-key-456"


def test_a_hung_server_is_terminated_then_killed_then_restarted() -> None:
    h = Harness(unhealthy_s=60, health_interval_s=10, stop_grace_s=20)
    url = "http://127.0.0.1:8080/health"
    h.healthy[url] = True
    h.at(0)
    h.at(10)
    hung = h.current("glimmer")
    hung.ignores_term = True
    h.healthy[url] = False
    for t in range(20, 70, 10):
        h.at(t)
    assert hung.signals == []  # 50 s without health: not yet
    h.at(70)
    assert hung.signals == ["TERM"]
    assert "glimmer unhealthy for 60 s; restarting it" in h.text()
    h.at(80)
    assert hung.signals == ["TERM"]
    h.at(90)
    assert hung.signals == ["TERM", "KILL"]
    h.at(91)  # reaped: it had been healthy, so the backoff is the first one
    assert h.logs[-1] == "glimmer restarts in 2 s"
    h.at(93)
    assert h.current("glimmer") is not hung


def test_a_server_that_never_got_healthy_is_left_to_load() -> None:
    h = Harness(unhealthy_s=60)
    for t in range(0, 3000, 10):  # a 17 GB first download takes a while
        h.at(t)
    assert h.procs[0].signals == []
    assert h.sup.state["glimmer"].starts == 1


def test_all_healthy_is_reported_once() -> None:
    h = Harness()
    h.healthy = {"http://127.0.0.1:8080/health": True, "http://127.0.0.1:8090/health": True}
    h.at(0)
    h.at(1)
    assert h.ready == 1
    assert "glimmer healthy after 1 s" in h.text()
    h.healthy["http://127.0.0.1:8090/health"] = False
    h.at(11)
    assert "sam /health is failing" in h.text()
    h.healthy["http://127.0.0.1:8090/health"] = True
    h.at(21)
    assert h.ready == 1


def test_a_command_that_cannot_start_is_retried() -> None:
    def broken(argv: Sequence[str], env: Mapping[str, str]) -> Any:
        raise FileNotFoundError(2, "No such file or directory")

    logs: list[str] = []
    now = [0.0]
    sup = sv.Supervisor(
        [service("glimmer", 8080)],
        spawn=broken,
        probe=lambda url: False,
        clock=lambda: now[0],
        say=logs.append,
    )
    sup.step()
    assert "glimmer could not start (FileNotFoundError: No such file or directory)" in logs
    assert sup.state["glimmer"].next_start == 2.0


def test_stop_terminates_both_and_kills_a_straggler() -> None:
    h = Harness(stop_grace_s=0)
    h.at(0)
    stubborn = h.current("sam")
    stubborn.ignores_term = True
    h.sup.stop()
    assert h.procs[0].signals == ["TERM"]
    assert stubborn.signals == ["TERM", "KILL"]
    assert "sam did not stop; killing it" in h.text()
    assert all(s.proc is None for s in h.sup.state.values())


def test_run_steps_until_asked_to_stop() -> None:
    h = Harness()
    ticks: list[float] = []
    asked = iter([False, False, True])
    assert h.sup.run(lambda: next(asked), sleep=ticks.append) == 0
    assert ticks == [1.0, 1.0]
    assert all(p.code == -15 for p in h.procs)


def test_child_env_has_bootstrap_until_dropped() -> None:
    sup = sv.Supervisor(
        [service("sam", 8090, bootstrap_env={"HF_TOKEN": "t"})],
        spawn=FakeProc,
        probe=lambda url: True,
    )
    assert sup.child_env(sup.services[0]) == {"HF_TOKEN": "t"}
    sup.step()
    sup.step()
    assert sup.child_env(sup.services[0]) == {}


@pytest.fixture
def health_server() -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200 if self.path == "/health" else 503)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_http_probe(health_server: str) -> None:
    assert sv.http_probe(f"{health_server}/health")
    assert not sv.http_probe(f"{health_server}/loading")
    assert not sv.http_probe("http://127.0.0.1:9/health", timeout_s=1)


def test_popen_starts_a_real_child() -> None:
    proc = sv.popen(
        [sys.executable, "-c", "import os; raise SystemExit(int(os.environ['C']))"], {"C": "3"}
    )
    assert proc.wait(timeout=30) == 3


def test_gpu_memory_is_logged_when_nvidia_smi_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sv.shutil, "which", lambda name: None)
    sv.gpu_memory()
    assert capsys.readouterr().err == ""
    smi = tmp_path / "nvidia-smi"
    smi.write_text("#!/bin/sh\necho 'NVIDIA RTX A6000, 49140 MiB, 31000 MiB, 18140 MiB'\n")
    smi.chmod(0o755)
    monkeypatch.setattr(sv.shutil, "which", lambda name: str(smi))
    sv.gpu_memory()
    assert (
        "gpu memory (name, total, used, free): NVIDIA RTX A6000, 49140 MiB"
        in capsys.readouterr().err
    )


def test_main_plan_and_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in sv.SECRET_VARS:
        monkeypatch.delenv(name, raising=False)
    assert sv.main(["--plan"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert [p["name"] for p in plan] == ["glimmer", "sam"]
    assert plan[0]["argv"][1:] == list(rp.GLIMMER_ARGS)
    assert plan[1]["health"] == "http://127.0.0.1:8090/health"
    assert sv.main(["--bogus"]) == 2
    assert "unknown arguments" in capsys.readouterr().err
    assert sv.main([]) == 2
    assert "missing LLAMA_API_KEY, SAM_API_KEY" in capsys.readouterr().err


def test_main_takes_the_secrets_out_of_its_own_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    seen: dict[str, Any] = {}

    def run(self: Any, stopping: Any, sleep: Any = None) -> int:
        seen["services"] = [s.name for s in self.services]
        seen["env"] = {k: sv.os.environ.get(k) for k in sv.SECRET_VARS}
        return 0

    handlers: dict[int, Any] = {}
    monkeypatch.setattr(
        sv.signal, "signal", lambda signum, handler: handlers.update({signum: handler})
    )
    monkeypatch.setattr(sv.Supervisor, "run", run)
    assert sv.main([]) == 0
    assert set(handlers) == {sv.signal.SIGTERM, sv.signal.SIGINT}
    assert seen["services"] == ["glimmer", "sam"]
    assert seen["env"] == dict.fromkeys(sv.SECRET_VARS)
    handlers[sv.signal.SIGTERM](sv.signal.SIGTERM, None)
    err = capsys.readouterr().err
    assert "received SIGTERM; stopping" in err
    assert "supervising glimmer (http://127.0.0.1:8080/health), sam" in err
    for secret in ("llama-key-123", "sam-key-456", "hf-token-789"):
        assert secret not in err
