"""Start the live stack when this Mac logs in: the API, the tunnel watchdog and keep-awake.

    uv run python scripts/ops/mac_services.py install     # write the launchd agents and start them
    uv run python scripts/ops/mac_services.py status
    uv run python scripts/ops/mac_services.py uninstall   # stop them and remove the agents

Three launchd agents in ~/Library/LaunchAgents, each restarted by launchd if it exits:

- `com.rulecam.api`: `scripts/ops/run_api_live.sh` (log `data/logs/api.log`).
- `com.rulecam.tunnel`: `scripts/ops/tunnel_watchdog.py`, which restarts the API after it starts
  a new tunnel, so the phone-pairing link has the new address (log `data/tunnel/watchdog.log`).
- `com.rulecam.awake`: `caffeinate -dims`, so the Mac does not sleep while it serves the demo.

macOS does not let launchd jobs read ~/Documents, ~/Desktop or ~/Downloads, so the repo must
live elsewhere (we keep it in ~/Code, with a link at the old place); `install` refuses otherwise.
"""

# ruff: noqa: S603, S607 - launchctl, fixed arguments

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
API = "com.rulecam.api"
TUNNEL = "com.rulecam.tunnel"
AWAKE = "com.rulecam.awake"
LABELS = (API, TUNNEL, AWAKE)
PROTECTED = ("Documents", "Desktop", "Downloads")
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# Where the agents go when ~/Library/LaunchAgents is not writable (they then run, but not at login).
FALLBACK_DIR = Path("data/launchd")


def protected_folder(root: Path, home: Path) -> str | None:
    """Return the macOS-protected home folder `root` is in, or None when launchd can read it."""
    for name in PROTECTED:
        if root.resolve().is_relative_to((home / name).resolve()):
            return name
    return None


def agents(root: Path, uid: int, uv: str) -> dict[str, dict[str, Any]]:
    """Return the launchd agent for each service, by label.

    Args:
        root: The repo (its real path, outside the protected folders).
        uid: The user whose GUI session runs the agents (for `launchctl kickstart`).
        uv: The `uv` executable.
    """

    def agent(label: str, args: list[str], log: Path) -> dict[str, Any]:
        return {
            "Label": label,
            "ProgramArguments": args,
            "WorkingDirectory": str(root),
            "EnvironmentVariables": {"PATH": PATH},
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 15,
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }

    restart_api = f"/bin/launchctl kickstart -k gui/{uid}/{API}"
    watchdog = [uv, "run", "python", "scripts/ops/tunnel_watchdog.py"]
    return {
        API: agent(
            API,
            ["/bin/bash", str(root / "scripts/ops/run_api_live.sh")],
            root / "data/logs/api.log",
        ),
        TUNNEL: agent(
            TUNNEL,
            [*watchdog, "--after-new-tunnel", restart_api],
            root / "data/tunnel/watchdog.log",
        ),
        AWAKE: agent(AWAKE, ["/usr/bin/caffeinate", "-dims"], root / "data/logs/awake.log"),
    }


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    """Run launchctl and return what it did (failures are reported by the caller)."""
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def stop(uid: int, label: str, wait_s: float = 30.0) -> None:
    """Stop an agent and wait until launchd has let it go (the API takes a SIGKILL, after 20 s)."""
    launchctl("bootout", f"gui/{uid}/{label}")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline and launchctl("print", f"gui/{uid}/{label}").returncode == 0:
        time.sleep(1)


def install(root: Path, uid: int) -> None:
    """Write the agents and (re)start them in this user's GUI session."""
    folder = protected_folder(root, Path.home())
    if folder is not None:
        raise SystemExit(f"{root} is in ~/{folder}, which launchd jobs cannot read: move the repo")
    uv = shutil.which("uv", path=PATH) or "uv"
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    target = AGENTS_DIR if os.access(AGENTS_DIR, os.W_OK) else root / FALLBACK_DIR
    target.mkdir(parents=True, exist_ok=True)
    for label, agent in agents(root, uid, uv).items():
        for log in {agent["StandardOutPath"], agent["StandardErrorPath"]}:
            Path(log).parent.mkdir(parents=True, exist_ok=True)
        plist = target / f"{label}.plist"
        stop(uid, label)
        plist.write_bytes(plistlib.dumps(agent))
        done = launchctl("bootstrap", f"gui/{uid}", str(plist))
        print(f"{label}: {'started' if done.returncode == 0 else done.stderr.strip()}")
    if target != AGENTS_DIR:
        print(
            f"{AGENTS_DIR} is not writable, so the agents run now but will NOT start at login.\n"
            f"Fix it with `sudo chown $USER {AGENTS_DIR}`, then run install again."
        )


def uninstall(uid: int, root: Path) -> None:
    """Stop the agents and remove their files."""
    for label in LABELS:
        stop(uid, label)
        for folder in (AGENTS_DIR, root / FALLBACK_DIR):
            if os.access(folder, os.W_OK):
                (folder / f"{label}.plist").unlink(missing_ok=True)
        print(f"{label}: removed")


def status(uid: int) -> None:
    """Print each agent's state and pid."""
    for label in LABELS:
        done = launchctl("print", f"gui/{uid}/{label}")
        fields = dict(
            line.strip().split(" = ", 1) for line in done.stdout.splitlines() if " = " in line
        )
        state = fields.get("state", "not loaded")
        print(f"{label}: {state}, pid {fields.get('pid', '-')}, runs {fields.get('runs', '-')}")


def main() -> None:
    """Parse the command and run it."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["install", "uninstall", "status"])
    command = parser.parse_args().command
    uid = os.getuid()
    if command == "install":
        install(ROOT, uid)
    elif command == "uninstall":
        uninstall(uid, ROOT)
    else:
        status(uid)


if __name__ == "__main__":
    main()
