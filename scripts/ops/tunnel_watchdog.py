"""Keep the API's public tunnel up and the Vercel site pointed at it.

    uv run python scripts/ops/tunnel_watchdog.py [--every 30] [--failures 4]

The site (the Vercel project `ui/` is linked to) reaches the API on this Mac through a Cloudflare
quick tunnel. A quick tunnel that loses the network for long is gone for good ("Tunnel not
found"), and a new one has a new address that is built into the site. Every `--every` seconds
this checks the API on 127.0.0.1, the tunnel and the site's `/rulecam-api/health`:

- API down: nothing to fix here; it waits (the tunnel cannot answer without the API).
- Tunnel down `--failures` times in a row, or no cloudflared running: it starts a new tunnel,
  waits until it answers, sets `NEXT_PUBLIC_RULECAM_API` on the Vercel project, runs
  `vercel deploy --prod` and writes `PUBLIC_TUNNEL_URL` in the env file.
- Tunnel up but the site failing `--failures` times in a row: it redeploys the site.

At start it keeps the tunnel in the env file's `PUBLIC_TUNNEL_URL` while that answers. Its
cloudflared runs in its own session, so stopping the watchdog leaves the tunnel up. Site deploys
are at least `--deploy-gap` seconds apart (Vercel Hobby allows 100 a day). The phone-pairing link
reads `PUBLIC_TUNNEL_URL` when the API starts, so `--after-new-tunnel` takes a command that
restarts the API (the login service passes `launchctl kickstart -k ...`).
"""

# ruff: noqa: S603, S607 - cloudflared, pgrep and the Vercel CLI, fixed arguments

from __future__ import annotations

import argparse
import logging
import re
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
HTTP_OK = 200
PROBE_TIMEOUT_S = 10.0
TUNNEL_URL = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")
REGISTERED = "Registered tunnel connection"
API_SETTING = "NEXT_PUBLIC_RULECAM_API"

log = logging.getLogger("tunnel_watchdog")


class Action(Enum):
    """What the watchdog does after a round of checks."""

    WAIT = "wait"
    NEW_TUNNEL = "new_tunnel"
    REDEPLOY = "redeploy"


@dataclass(frozen=True)
class Probe:
    """One round of checks."""

    api: bool
    tunnel: bool
    site: bool
    cloudflared: bool


@dataclass(frozen=True)
class Streak:
    """Failures in a row so far."""

    tunnel: int = 0
    site: int = 0


def decide(probe: Probe, streak: Streak, limit: int) -> tuple[Action, Streak]:
    """Pick the next action from this round's checks and the failures before it.

    Args:
        probe: This round's checks.
        streak: Failures in a row before this round.
        limit: Failures in a row that call for a fix.

    Returns:
        The action and the streak to carry into the next round.
    """
    if not probe.api:
        return Action.WAIT, Streak()
    if not probe.tunnel:
        failures = streak.tunnel + 1
        if failures >= limit or not probe.cloudflared:
            return Action.NEW_TUNNEL, Streak()
        return Action.WAIT, Streak(tunnel=failures)
    if not probe.site:
        failures = streak.site + 1
        if failures >= limit:
            return Action.REDEPLOY, Streak()
        return Action.WAIT, Streak(site=failures)
    return Action.WAIT, Streak()


def tunnel_url(log_text: str) -> str | None:
    """Return the quick tunnel's address once cloudflared has registered a connection.

    Args:
        log_text: cloudflared's output so far.

    Returns:
        The `https://<name>.trycloudflare.com` address, or None while it is not serving yet.
    """
    match = TUNNEL_URL.search(log_text)
    return match.group(0) if match and REGISTERED in log_text else None


def read_setting(text: str, key: str) -> str | None:
    """Return `key`'s value in an env file's text, or None when it is not set."""
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def with_setting(text: str, key: str, value: str) -> str:
    """Return an env file's text with `key` set to `value` (replaced in place, else appended)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            return "\n".join(lines) + "\n"
    return "\n".join([*lines, f"{key}={value}"]) + "\n"


def answers(url: str) -> bool:
    """Return True when `GET url` answers 200."""
    try:
        return httpx.get(url, timeout=PROBE_TIMEOUT_S).status_code == HTTP_OK
    except httpx.HTTPError:
        return False


def tunnel_pids(port: int) -> list[int]:
    """Return the pids of cloudflared processes serving 127.0.0.1:`port`."""
    found = subprocess.run(
        ["pgrep", "-f", f"cloudflared tunnel .*127.0.0.1:{port}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [int(pid) for pid in found.stdout.split()]


@dataclass
class Watchdog:
    """The tunnel and site this Mac serves, and how to replace them."""

    port: int
    env_file: Path
    ui_dir: Path
    log_dir: Path
    cloudflared: str
    vercel: list[str]
    deploy_gap_s: float
    after_new_tunnel: list[str] = field(default_factory=list)
    url: str | None = None
    last_deploy: float = float("-inf")
    clock: Callable[[], float] = time.monotonic

    @property
    def site(self) -> str | None:
        """The public site's address (`PUBLIC_UI_URL` in the env file)."""
        return read_setting(self.env_file.read_text(), "PUBLIC_UI_URL")

    def probe(self) -> Probe:
        """Check the API, the tunnel and the site once."""
        site = self.site
        return Probe(
            api=answers(f"http://127.0.0.1:{self.port}/health"),
            tunnel=self.url is not None and answers(f"{self.url}/health"),
            site=site is not None and answers(f"{site}/rulecam-api/health"),
            cloudflared=bool(tunnel_pids(self.port)),
        )

    def new_tunnel(self, wait_s: float = 90.0) -> bool:
        """Replace any tunnel to the API with a new one, then point the site at it.

        Returns:
            True when the new tunnel answers (the site deploy may still fail; the site check
            retries it).
        """
        for pid in tunnel_pids(self.port):
            subprocess.run(["kill", str(pid)], check=False)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        out = self.log_dir / f"cloudflared-{time.strftime('%Y%m%dT%H%M%S')}.log"
        with out.open("w") as sink:
            process = subprocess.Popen(
                [
                    self.cloudflared,
                    "tunnel",
                    "--no-autoupdate",
                    "--url",
                    f"http://127.0.0.1:{self.port}",
                ],
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        deadline = self.clock() + wait_s
        while self.clock() < deadline:
            url = tunnel_url(out.read_text())
            if url and answers(f"{url}/health"):
                log.info("new tunnel %s (pid %d, log %s)", url, process.pid, out)
                self.switch_to(url)
                return True
            if process.poll() is not None:
                break
            time.sleep(2)
        log.warning("no working tunnel after %.0f s; see %s", wait_s, out)
        process.kill()
        return False

    def switch_to(self, url: str) -> None:
        """Use `url` from now on: write it in the env file, redeploy the site, run the hook."""
        self.url = url
        self.env_file.write_text(with_setting(self.env_file.read_text(), "PUBLIC_TUNNEL_URL", url))
        self.redeploy()
        if self.after_new_tunnel:
            done = subprocess.run(
                self.after_new_tunnel, capture_output=True, text=True, check=False
            )
            log.info("after-new-tunnel command exited %d", done.returncode)

    def redeploy(self) -> bool:
        """Set the site's API address to the tunnel and deploy it to production."""
        if self.url is None:
            return False
        since = self.clock() - self.last_deploy
        if since < self.deploy_gap_s:
            log.info("site deploy held: the last one was %.0f s ago", since)
            return False
        self.last_deploy = self.clock()
        self._vercel("env", "rm", API_SETTING, "production", "--yes")
        added = self._vercel("env", "add", API_SETTING, "production", stdin=self.url)
        deployed = added and self._vercel("deploy", "--prod", "--yes")
        log.info("site deploy for %s: %s", self.url, "done" if deployed else "FAILED")
        return deployed

    def _vercel(self, *args: str, stdin: str | None = None) -> bool:
        done = subprocess.run(
            [*self.vercel, *args],
            cwd=self.ui_dir,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
            log.warning("vercel %s exited %d: %s", args[0], done.returncode, " | ".join(tail))
        return done.returncode == 0

    def adopt(self) -> None:
        """Keep the tunnel named in the env file when it answers."""
        url = read_setting(self.env_file.read_text(), "PUBLIC_TUNNEL_URL")
        if url and tunnel_pids(self.port) and answers(f"{url}/health"):
            self.url = url
            log.info("watching the running tunnel %s", url)

    def run(self, every_s: float, limit: int) -> None:
        """Check every `every_s` seconds and fix what `decide` asks for, until stopped."""
        self.adopt()
        streak = Streak()
        all_up = Probe(api=True, tunnel=True, site=True, cloudflared=True)
        was_up = True
        while True:
            probe = self.probe()
            action, streak = decide(probe, streak, limit)
            if probe != all_up:
                log.info("%s -> %s %s", probe, action.value, streak)
            elif not was_up:
                log.info("all checks pass again (tunnel %s)", self.url)
            was_up = probe == all_up
            if action is Action.NEW_TUNNEL:
                self.new_tunnel()
            elif action is Action.REDEPLOY:
                self.redeploy()
            time.sleep(every_s)


def main() -> None:
    """Parse the options and watch until stopped."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--every", type=float, default=30.0, help="seconds between checks")
    parser.add_argument("--failures", type=int, default=4, help="failures in a row before a fix")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.live")
    parser.add_argument("--ui-dir", type=Path, default=ROOT / "ui")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "data" / "tunnel")
    parser.add_argument("--cloudflared", default=shutil.which("cloudflared") or "cloudflared")
    parser.add_argument("--vercel", default="npx --yes vercel@65.7.0", help="Vercel CLI command")
    parser.add_argument("--deploy-gap", type=float, default=300.0, help="seconds between deploys")
    parser.add_argument("--after-new-tunnel", default="", help="command to run after a new tunnel")
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per check is noise
    Watchdog(
        port=options.port,
        env_file=options.env_file,
        ui_dir=options.ui_dir,
        log_dir=options.log_dir,
        cloudflared=options.cloudflared,
        vercel=shlex.split(options.vercel),
        deploy_gap_s=options.deploy_gap,
        after_new_tunnel=shlex.split(options.after_new_tunnel),
    ).run(options.every, options.failures)


if __name__ == "__main__":
    main()
