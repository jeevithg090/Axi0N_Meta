"""Supervisor of the one-GPU pod (WS3/WS5; deploy/gpu.Dockerfile, docs/deploy.md "One GPU").

One 48 GB GPU runs both model servers of a site:

- **glimmer**: llama.cpp's `llama-server` with Muse Glimmer 30B (Q4_K_M + mmproj) on :8080, the
  same arguments as the two-pod deploy (`scripts/deploy/runpod.py` `GLIMMER_ARGS`), with the
  context (`GLIMMER_CTX`, 32768) and slots (`GLIMMER_PARALLEL`, 2) sized so SAM keeps headroom;
- **sam**: `sam-entrypoint.sh _serve` (fills the SAM 3.1 checkpoint cache once, then runs
  `rulecam.perception.sam_server`, image mode for the runtime) on :8090.

Each service is restarted when it exits (backoff 2 s doubling to 60 s, reset after a run that
became healthy or lasted `stable_s`), and when its `/health` stops answering for `unhealthy_s`
after it had been healthy (a hung server). SIGTERM/SIGINT stop both (TERM, then KILL after
`stop_grace_s`).

Secrets come in the environment (the entrypoint read them from RunPod secrets or mode-600
files) and are taken out of it at once, so each child gets only its own: `LLAMA_API_KEY` goes
to llama-server alone, `SAM_API_KEY` to the SAM server alone, and `HF_TOKEN` only to the SAM
service's first start-ups, until it has been healthy once (the checkpoint is then cached; the
SAM entrypoint also unsets it before the server starts). No value is ever logged or put in argv.

Stdlib only: it runs with the SAM venv's Python. `--plan` prints both commands (no secrets).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

NAME = "rulecam-gpu"
SECRET_VARS = ("LLAMA_API_KEY", "SAM_API_KEY", "HF_TOKEN")
LLAMA_SERVER = "/opt/llama/llama-server"
LLAMA_LIBRARY_PATH = "/opt/llama:/opt/llama/cuda"  # its ggml backends, then torch's cuBLAS
SAM_ENTRYPOINT = "/opt/rulecam/deploy/sam-entrypoint.sh"
DEFAULT_CTX = "32768"  # tokens, all slots together (16k each): KV cache about 2 GB
DEFAULT_PARALLEL = "2"
GLIMMER_MODEL = (
    "--hf-repo", "meta-models/Muse-Glimmer-30B-GGUF",
    "--hf-file", "Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf",
    "--mmproj-url",
    "https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/resolve/main/"
    "mmproj-Muse-Glimmer-30B-Q4_K_M.gguf",
    "-a", "muse-glimmer-30b", "-ngl", "99",
)  # fmt: skip
GLIMMER_SAMPLING = ("--jinja", "--temp", "1.0", "--top-p", "0.95", "--top-k", "64")


class Proc(Protocol):
    """The part of `subprocess.Popen` the supervisor uses."""

    pid: int

    def poll(self) -> int | None:
        """Exit code, or `None` while running."""

    def terminate(self) -> None:
        """Send SIGTERM."""

    def kill(self) -> None:
        """Send SIGKILL."""

    def wait(self, timeout: float | None = None) -> int:
        """Wait for the exit code (raises `subprocess.TimeoutExpired`)."""


Spawn = Callable[[Sequence[str], Mapping[str, str]], Proc]
Probe = Callable[[str], bool]


@dataclass(frozen=True)
class Service:
    """One supervised server.

    Attributes:
        name: Log name (`glimmer`, `sam`).
        argv: The command.
        env: Its full environment (with its own secret only).
        health_url: `GET` answers 200 when it serves.
        bootstrap_env: Extra variables for start-ups until it has been healthy once.
    """

    name: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    health_url: str
    bootstrap_env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Policy:
    """Restart and health timings, in seconds."""

    backoff_s: float = 2.0
    max_backoff_s: float = 60.0
    stable_s: float = 120.0
    unhealthy_s: float = 180.0
    health_interval_s: float = 10.0
    stop_grace_s: float = 20.0
    tick_s: float = 1.0


@dataclass
class _State:
    proc: Proc | None = None
    started: float = 0.0
    next_start: float = 0.0
    crashes: int = 0  # exits in a row without a stable or healthy run
    healthy: bool = False
    healthy_this_run: bool = False
    last_ok: float = 0.0
    last_probe: float | None = None
    stopping_since: float | None = None
    starts: int = 0


def log(message: str) -> None:
    """One line on stderr, like the shell entrypoints' `log`."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sys.stderr.write(f"{stamp} {NAME}: {message}\n")
    sys.stderr.flush()


class Supervisor:
    """Keeps every service running; `step` does one pass and is driven by `run`."""

    def __init__(
        self,
        services: Sequence[Service],
        *,
        spawn: Spawn,
        probe: Probe,
        clock: Callable[[], float] = time.monotonic,
        policy: Policy | None = None,
        on_all_healthy: Callable[[], None] | None = None,
        say: Callable[[str], None] = log,
    ) -> None:
        """Supervise `services`.

        Args:
            services: What to run.
            spawn: Starts a command with an environment.
            probe: Whether a health URL answers 200.
            clock: Monotonic seconds.
            policy: Timings.
            on_all_healthy: Called once, the first time every service is healthy (logs GPU
                memory on the pod).
            say: Log sink.
        """
        self.services = tuple(services)
        self._spawn = spawn
        self._probe = probe
        self._clock = clock
        self.policy = policy or Policy()
        self._on_all_healthy = on_all_healthy
        self._say = say
        self._bootstrap = {s.name: dict(s.bootstrap_env) for s in self.services}
        self.state = {s.name: _State() for s in self.services}
        self._reported = False

    def step(self) -> None:
        """Reap exits, start what is due, probe health and restart hung servers."""
        now = self._clock()
        for service in self.services:
            state = self.state[service.name]
            if state.proc is None:
                if now >= state.next_start:
                    self._start(service, state, now)
                continue
            code = state.proc.poll()
            if code is not None:
                self._exited(service, state, now, code)
            elif state.stopping_since is not None:
                if now - state.stopping_since >= self.policy.stop_grace_s:
                    self._say(f"{service.name} ignored SIGTERM; killing pid {state.proc.pid}")
                    state.proc.kill()
            else:
                self._check(service, state, now)
        if not self._reported and all(s.healthy for s in self.state.values()):
            self._reported = True
            self._say("all services healthy")
            if self._on_all_healthy is not None:
                self._on_all_healthy()

    def child_env(self, service: Service) -> dict[str, str]:
        """The environment of the next start-up of `service`."""
        return {**service.env, **self._bootstrap[service.name]}

    def _start(self, service: Service, state: _State, now: float) -> None:
        try:
            state.proc = self._spawn(service.argv, self.child_env(service))
        except OSError as exc:
            self._say(f"{service.name} could not start ({type(exc).__name__}: {exc.strerror})")
            self._schedule_restart(service, state, now)
            return
        state.starts += 1
        state.started = now
        state.healthy = state.healthy_this_run = False
        state.last_probe = state.stopping_since = None
        self._say(f"{service.name} started (pid {state.proc.pid}, start {state.starts})")

    def _exited(self, service: Service, state: _State, now: float, code: int) -> None:
        ran = now - state.started
        self._say(f"{service.name} exited with {code} after {ran:.0f} s")
        if state.healthy_this_run or ran >= self.policy.stable_s:
            state.crashes = 0
        state.proc = None
        state.healthy = state.healthy_this_run = False
        state.stopping_since = None
        self._schedule_restart(service, state, now)

    def _schedule_restart(self, service: Service, state: _State, now: float) -> None:
        state.crashes += 1
        delay = min(self.policy.max_backoff_s, self.policy.backoff_s * 2 ** (state.crashes - 1))
        state.next_start = now + delay
        self._say(f"{service.name} restarts in {delay:.0f} s")

    def _check(self, service: Service, state: _State, now: float) -> None:
        due = state.last_probe is None or now - state.last_probe >= self.policy.health_interval_s
        if not due:
            return
        state.last_probe = now
        if self._probe(service.health_url):
            state.last_ok = now
            if not state.healthy:
                self._say(f"{service.name} healthy after {now - state.started:.0f} s")
            state.healthy = state.healthy_this_run = True
            if self._bootstrap[service.name]:
                self._bootstrap[service.name].clear()  # e.g. HF_TOKEN: the cache is filled
                self._say(f"{service.name}: start-up secrets dropped")
            return
        if state.healthy:
            self._say(f"{service.name} /health is failing")
        state.healthy = False
        if state.healthy_this_run and now - state.last_ok >= self.policy.unhealthy_s:
            self._say(f"{service.name} unhealthy for {now - state.last_ok:.0f} s; restarting it")
            state.stopping_since = now
            if state.proc is not None:
                state.proc.terminate()

    def stop(self) -> None:
        """Terminate every child, then kill what is left after the grace period."""
        running = [(s.name, st.proc) for s, st in self._pairs() if st.proc is not None]
        for name, proc in running:
            self._say(f"stopping {name} (pid {proc.pid})")
            proc.terminate()
        deadline = self._clock() + self.policy.stop_grace_s
        for name, proc in running:
            try:
                proc.wait(timeout=max(0.0, deadline - self._clock()))
            except subprocess.TimeoutExpired:
                self._say(f"{name} did not stop; killing it")
                proc.kill()
                proc.wait()
        for state in self.state.values():
            state.proc = None

    def _pairs(self) -> list[tuple[Service, _State]]:
        return [(s, self.state[s.name]) for s in self.services]

    def run(self, stopping: Callable[[], bool], sleep: Callable[[float], None] = time.sleep) -> int:
        """Step until `stopping()` is true, then stop the children."""
        while not stopping():
            self.step()
            sleep(self.policy.tick_s)
        self.stop()
        return 0


# ---------------------------------------------------------------------------------------------
# The pod's two services


def glimmer_args(env: Mapping[str, str]) -> list[str]:
    """llama-server's arguments: the documented Glimmer args, context and slots from `env`."""
    return [
        *GLIMMER_MODEL,
        "-c", env.get("GLIMMER_CTX") or DEFAULT_CTX,
        "-np", env.get("GLIMMER_PARALLEL") or DEFAULT_PARALLEL,
        "--host", "0.0.0.0",  # noqa: S104 - behind the RunPod HTTPS proxy, key required
        "--port", env.get("GLIMMER_PORT") or "8080",
        *GLIMMER_SAMPLING,
    ]  # fmt: skip


def split_secrets(env: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Separate the secrets (`SECRET_VARS`, non-empty) from the rest of `env`."""
    secrets = {k: env[k] for k in SECRET_VARS if env.get(k)}
    rest = {k: v for k, v in env.items() if k not in SECRET_VARS}
    return rest, secrets


def services(env: Mapping[str, str]) -> list[Service]:
    """Glimmer and SAM, each with its own secret only.

    Raises:
        KeyError: When `LLAMA_API_KEY` or `SAM_API_KEY` is missing.
    """
    base, secrets = split_secrets(env)
    missing = [k for k in ("LLAMA_API_KEY", "SAM_API_KEY") if k not in secrets]
    if missing:
        raise KeyError(", ".join(missing))
    llama_paths = [env.get("GPU_LLAMA_LIBRARY_PATH") or LLAMA_LIBRARY_PATH]
    if base.get("LD_LIBRARY_PATH"):
        llama_paths.append(base["LD_LIBRARY_PATH"])
    glimmer_port = env.get("GLIMMER_PORT") or "8080"
    sam_port = env.get("SAM_PORT") or "8090"
    glimmer = Service(
        name="glimmer",
        argv=(env.get("GPU_LLAMA_SERVER") or LLAMA_SERVER, *glimmer_args(env)),
        env={
            **base,
            "LLAMA_API_KEY": secrets["LLAMA_API_KEY"],
            "LLAMA_CACHE": env.get("LLAMA_CACHE") or "/workspace/llama-cache",
            "LD_LIBRARY_PATH": ":".join(llama_paths),
        },
        health_url=f"http://127.0.0.1:{glimmer_port}/health",
    )
    hf = {"HF_TOKEN": secrets["HF_TOKEN"]} if "HF_TOKEN" in secrets else {}
    sam = Service(
        name="sam",
        argv=(env.get("GPU_SAM_ENTRYPOINT") or SAM_ENTRYPOINT, "_serve"),
        env={**base, "SAM_API_KEY": secrets["SAM_API_KEY"], "SAM_PORT": sam_port},
        health_url=f"http://127.0.0.1:{sam_port}/health",
        bootstrap_env=hf,
    )
    return [glimmer, sam]


def http_probe(url: str, *, timeout_s: float = 4.0) -> bool:
    """`GET url` answers 200 (loopback only)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310 - loopback
            return int(response.status) == 200  # noqa: PLR2004
    except (urllib.error.URLError, OSError, ValueError):
        return False


def popen(argv: Sequence[str], env: Mapping[str, str]) -> Proc:
    """Start a child with inherited stdout/stderr (the container log)."""
    return subprocess.Popen(list(argv), env=dict(env))  # noqa: S603 - fixed commands


def gpu_memory() -> None:
    """Log the GPU's memory once both servers are up (the budget in docs/deploy.md)."""
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return
    query = "--query-gpu=name,memory.total,memory.used,memory.free"
    result = subprocess.run(  # noqa: S603 - fixed argv
        [smi, query, "--format=csv,noheader"], capture_output=True, text=True, check=False
    )
    if result.returncode == 0:
        log(f"gpu memory (name, total, used, free): {result.stdout.strip()}")


def plan(env: Mapping[str, str]) -> list[dict[str, object]]:
    """Both commands and health URLs, for `--plan` (no secret values)."""
    placeholders = {**env, "LLAMA_API_KEY": "-", "SAM_API_KEY": "-"}
    return [
        {"name": s.name, "argv": list(s.argv), "health": s.health_url}
        for s in services(placeholders)
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Run both servers until SIGTERM/SIGINT; `--plan` prints the commands instead."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--plan"]:
        sys.stdout.write(json.dumps(plan(os.environ), indent=2) + "\n")
        return 0
    if args:
        log(f"unknown arguments: {' '.join(args)} (none, or --plan)")
        return 2
    try:
        children = services(os.environ)
    except KeyError as exc:
        log(f"ERROR: missing {exc.args[0]} (the entrypoint reads them from secrets or files)")
        return 2
    for name in SECRET_VARS:
        os.environ.pop(name, None)  # nothing else the supervisor starts inherits them
    stop = {"requested": False}

    def request_stop(signum: int, _frame: object) -> None:
        log(f"received {signal.Signals(signum).name}; stopping")
        stop["requested"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    supervisor = Supervisor(children, spawn=popen, probe=http_probe, on_all_healthy=gpu_memory)
    log("supervising " + ", ".join(f"{s.name} ({s.health_url})" for s in children))
    return supervisor.run(lambda: stop["requested"])


if __name__ == "__main__":
    sys.exit(main())
