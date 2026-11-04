"""scripts/ops/mac_services.py: the launchd agents it writes, offline (launchctl is not run)."""

from __future__ import annotations

import importlib.util
import plistlib
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "mac_services.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mac_services", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mac = load_script()


def test_repos_in_folders_launchd_cannot_read_are_refused(tmp_path: Path) -> None:
    for name in ("Documents", "Desktop", "Downloads"):
        assert mac.protected_folder(tmp_path / name / "Meta Hack" / "repo", tmp_path) == name
    assert mac.protected_folder(tmp_path / "Code" / "repo", tmp_path) is None
    assert mac.protected_folder(tmp_path / "DocumentsOld" / "repo", tmp_path) is None


def test_link_into_a_protected_folder_counts_as_protected(tmp_path: Path) -> None:
    real = tmp_path / "Documents" / "repo"
    real.mkdir(parents=True)
    (tmp_path / "Code").mkdir()
    (tmp_path / "Code" / "repo").symlink_to(real)
    assert mac.protected_folder(tmp_path / "Code" / "repo", tmp_path) == "Documents"


def test_agents_run_the_three_services_from_the_repo(tmp_path: Path) -> None:
    made = mac.agents(tmp_path, 501, "/opt/homebrew/bin/uv")
    assert set(made) == {"com.rulecam.api", "com.rulecam.tunnel", "com.rulecam.awake"}
    for label, agent in made.items():
        assert agent["Label"] == label
        assert agent["WorkingDirectory"] == str(tmp_path)
        assert agent["RunAtLoad"] is True
        assert agent["KeepAlive"] is True
        assert "/opt/homebrew/bin" in agent["EnvironmentVariables"]["PATH"]
        assert plistlib.loads(plistlib.dumps(agent)) == agent
    assert made["com.rulecam.api"]["ProgramArguments"] == [
        "/bin/bash",
        str(tmp_path / "scripts/ops/run_api_live.sh"),
    ]
    assert made["com.rulecam.awake"]["ProgramArguments"] == ["/usr/bin/caffeinate", "-dims"]


def test_watchdog_restarts_the_api_agent_after_a_new_tunnel(tmp_path: Path) -> None:
    args = mac.agents(tmp_path, 501, "/opt/homebrew/bin/uv")["com.rulecam.tunnel"][
        "ProgramArguments"
    ]
    assert args[:4] == ["/opt/homebrew/bin/uv", "run", "python", "scripts/ops/tunnel_watchdog.py"]
    hook = args[args.index("--after-new-tunnel") + 1]
    assert hook == "/bin/launchctl kickstart -k gui/501/com.rulecam.api"
