"""scripts/ops/tunnel_watchdog.py: when to replace the tunnel or redeploy, offline."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "tunnel_watchdog.py"

FAKE_VERCEL = """
import json, os, sys
with open(os.environ["VERCEL_LOG"], "a") as log:
    log.write(json.dumps({"args": sys.argv[1:], "stdin": sys.stdin.read()}) + "\\n")
sys.exit(int(os.environ.get("FAIL_" + sys.argv[1] + "_" + sys.argv[2].replace("-", ""), "0")))
"""


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tunnel_watchdog", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


wd = load_script()
HEALTHY = wd.Probe(api=True, tunnel=True, site=True, cloudflared=True)


def probe(**changes: bool) -> Any:
    return wd.Probe(**{**HEALTHY.__dict__, **changes})


def test_healthy_round_waits_and_clears_the_streak() -> None:
    assert wd.decide(HEALTHY, wd.Streak(tunnel=2, site=1), 4) == (wd.Action.WAIT, wd.Streak())


def test_api_down_waits_without_touching_the_tunnel() -> None:
    down = probe(api=False, tunnel=False, site=False)
    assert wd.decide(down, wd.Streak(tunnel=3), 4) == (wd.Action.WAIT, wd.Streak())


def test_tunnel_is_replaced_after_the_failure_limit() -> None:
    streak = wd.Streak()
    for _ in range(3):
        action, streak = wd.decide(probe(tunnel=False, site=False), streak, 4)
        assert action is wd.Action.WAIT
    assert streak == wd.Streak(tunnel=3)
    assert wd.decide(probe(tunnel=False, site=False), streak, 4)[0] is wd.Action.NEW_TUNNEL


def test_tunnel_is_replaced_at_once_when_no_cloudflared_runs() -> None:
    gone = probe(tunnel=False, site=False, cloudflared=False)
    assert wd.decide(gone, wd.Streak(), 4) == (wd.Action.NEW_TUNNEL, wd.Streak())


def test_site_is_redeployed_after_the_failure_limit() -> None:
    action, streak = wd.decide(probe(site=False), wd.Streak(site=2), 4)
    assert (action, streak) == (wd.Action.WAIT, wd.Streak(site=3))
    assert wd.decide(probe(site=False), streak, 4)[0] is wd.Action.REDEPLOY


def test_tunnel_url_waits_for_a_registered_connection() -> None:
    requesting = "INF Requesting new quick Tunnel on https://api.trycloudflare.com...\n"
    created = requesting + "INF |  https://totally-new-name.trycloudflare.com  |\n"
    assert wd.tunnel_url(requesting) is None
    assert wd.tunnel_url(created) is None
    registered = created + "INF Registered tunnel connection connIndex=0\n"
    assert wd.tunnel_url(registered) == "https://totally-new-name.trycloudflare.com"


def test_settings_are_read_replaced_and_appended() -> None:
    text = "A=1\nPUBLIC_TUNNEL_URL=https://old.trycloudflare.com\nB=2\n"
    assert wd.read_setting(text, "PUBLIC_TUNNEL_URL") == "https://old.trycloudflare.com"
    assert wd.read_setting(text, "MISSING") is None
    new = wd.with_setting(text, "PUBLIC_TUNNEL_URL", "https://new.trycloudflare.com")
    assert new == "A=1\nPUBLIC_TUNNEL_URL=https://new.trycloudflare.com\nB=2\n"
    assert wd.with_setting("A=1\n", "C", "3") == "A=1\nC=3\n"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def watchdog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Path, Clock]:
    fake = tmp_path / "fake_vercel.py"
    fake.write_text(FAKE_VERCEL)
    calls = tmp_path / "vercel.jsonl"
    monkeypatch.setenv("VERCEL_LOG", str(calls))
    clock = Clock()
    dog = wd.Watchdog(
        port=8000,
        env_file=tmp_path / ".env.live",
        ui_dir=tmp_path,
        log_dir=tmp_path / "logs",
        cloudflared="cloudflared",
        vercel=[sys.executable, str(fake)],
        deploy_gap_s=300.0,
        url="https://new.trycloudflare.com",
        clock=clock,
    )
    return dog, calls, clock


def read_calls(calls: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in calls.read_text().splitlines()]


def test_redeploy_sets_the_api_address_then_deploys(watchdog: tuple[Any, Path, Clock]) -> None:
    dog, calls, _ = watchdog
    assert dog.redeploy()
    made = read_calls(calls)
    assert [c["args"] for c in made] == [
        ["env", "rm", "NEXT_PUBLIC_RULECAM_API", "production", "--yes"],
        ["env", "add", "NEXT_PUBLIC_RULECAM_API", "production"],
        ["deploy", "--prod", "--yes"],
    ]
    assert made[1]["stdin"] == "https://new.trycloudflare.com"


def test_deploys_wait_for_the_gap(watchdog: tuple[Any, Path, Clock]) -> None:
    dog, calls, clock = watchdog
    assert dog.redeploy()
    clock.now += 299
    assert not dog.redeploy()
    assert len(read_calls(calls)) == 3
    clock.now += 2
    assert dog.redeploy()
    assert len(read_calls(calls)) == 6


def test_no_deploy_when_the_address_cannot_be_set(
    watchdog: tuple[Any, Path, Clock], monkeypatch: pytest.MonkeyPatch
) -> None:
    dog, calls, _ = watchdog
    monkeypatch.setenv("FAIL_env_add", "1")
    assert not dog.redeploy()
    assert [c["args"][0:2] for c in read_calls(calls)] == [["env", "rm"], ["env", "add"]]
