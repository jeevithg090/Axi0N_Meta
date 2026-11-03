"""`rulecam scorecard --checks auto`: every automated check, against fakes (no network)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from rulecam.cli import app
from rulecam.eval import autochecks as auto
from rulecam.eval.autochecks import AutoConfig, checks_yaml, merge_checks, run_auto_checks
from rulecam.eval.scorecard import Check, Checks, build_scorecard, load_checks

REPO = Path(__file__).resolve().parents[2]
API = "https://api.example"
UI = "https://ui.example"
CHAIN = b'{"seq":1}\n'
cli = CliRunner()


def site(**overrides: httpx.Response | Exception) -> httpx.Client:
    """A healthy public deployment; `overrides` replaces the reply of one URL path."""
    metrics = {"response": {"firewall": "degraded", "firewall_reason": "HF_TOKEN is not set"}}
    replies: dict[str, httpx.Response | Exception] = {
        f"{API}/evidence/days": httpx.Response(200, json=["2026-10-31", "2026-11-01"]),
        f"{API}/evidence/2026-11-01/chain": httpx.Response(403),
        f"{API}/evidence/2026-11-01/hashes": httpx.Response(200, json={"n": 3, "hashes": []}),
        f"{API}/metrics": httpx.Response(200, text="# HELP x\nrulecam_runtime_lag_seconds 0.0\n"),
        f"{API}/runtime/metrics": httpx.Response(200, json=metrics),
        f"{API}/auth/me": httpx.Response(200, json={"owner": False, "login_enabled": False}),
        f"{UI}/demo": httpx.Response(200, text="<html>"),
        f"{UI}/verify/index.html": httpx.Response(200, text="<html>"),
        f"{UI}/golden/warehouse_driver/chain.jsonl": httpx.Response(200, content=CHAIN),
    }
    for key, value in overrides.items():
        replies[key] = value

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url.copy_with(query=None))
        reply = replies.get(url, httpx.Response(404))
        if isinstance(reply, Exception):
            raise reply
        return reply

    return httpx.Client(transport=httpx.MockTransport(handler))


def gh(
    conclusion: str = "success", *, found: bool = True
) -> Callable[[Sequence[str]], tuple[int, str]]:
    """A fake command runner for `gh api` and the deploy dry run."""

    def run(argv: Sequence[str]) -> tuple[int, str]:
        if argv[0] == "gh" and argv[2].endswith("per_page=1"):
            return (0, "41\n") if found else (1, "")
        if argv[0] == "gh":
            jobs = [{"name": "guardrails", "conclusion": conclusion, "html_url": "https://ci/41"}]
            return 0, json.dumps(jobs)
        return 0, "UI https://ui\nrunning cost about $1.42/h\n"

    return run


def config(tmp_path: Path, **extra: object) -> AutoConfig:
    chain = tmp_path / "chain.jsonl"
    chain.write_bytes(CHAIN)
    return AutoConfig(api=API, ui=UI, golden_chain=chain, **extra)  # type: ignore[arg-type]


def write_json(path: Path, data: object) -> Path:
    path.write_text("noise\n" + json.dumps(data) + "\n", encoding="utf-8")
    return path


def test_a_healthy_deployment(tmp_path: Path) -> None:
    restart = write_json(
        tmp_path / "restart.json",
        {"passed": True, "started_at": "t", "up_after_s": 5, "plan_restored": "plan-1",
         "checks": {"plan_restored": True}, "note": "process restart"},
    )  # fmt: skip
    budget = write_json(
        tmp_path / "budget.json",
        {"passed": True, "started_at": "t", "test_budget": 0.5, "alarm_seen": True,
         "over_budget": ["phone-1"], "restored_budget": 12.0},
    )  # fmt: skip
    checks = run_auto_checks(
        config(tmp_path, restart_evidence=restart, budget_evidence=budget),
        http=site(),
        run=gh(),
    )
    passed = {k for k, c in checks.items() if c.passed is True}
    assert passed == {
        "guardrail_tests",
        "evidence_routes_private",
        "survives_restart",
        "budget_alarm",
        "metrics_dashboard",
        "golden_replay_live",
    }
    assert checks["scene_text_firewall"].passed is False
    assert "degraded: HF_TOKEN is not set" in checks["scene_text_firewall"].evidence
    assert checks["one_command_deploy"].passed is None  # pending the GHCR decision
    assert "dry run passes" in checks["one_command_deploy"].evidence
    assert checks["owner_login"].passed is None
    assert "passphrase" in checks["owner_login"].evidence
    for check in checks.values():
        assert check.evidence  # never a result without what was observed


def test_failures_are_failures_with_evidence(tmp_path: Path) -> None:
    http = site(**{
        f"{API}/evidence/2026-11-01/chain": httpx.Response(200, text="records"),
        f"{API}/metrics": httpx.Response(200, text="# nothing\n"),
        f"{UI}/golden/warehouse_driver/chain.jsonl": httpx.Response(200, content=b"other"),
        f"{API}/auth/me": httpx.Response(200, json={"login_enabled": True}),
        f"{API}/runtime/metrics": httpx.Response(200, json={"response": {"firewall": "on"}}),
    })  # fmt: skip
    restart = write_json(
        tmp_path / "r.json",
        {"passed": True, "checks": {"plan_restored": True, "plan_listed_approved": False}},
    )
    budget = write_json(tmp_path / "b.json", {"passed": False, "alarm_seen": False})
    checks = run_auto_checks(
        config(tmp_path, restart_evidence=restart, budget_evidence=budget),
        http=http,
        run=gh("failure"),
    )
    assert checks["evidence_routes_private"].passed is False
    assert "chain → 200" in checks["evidence_routes_private"].evidence
    assert checks["metrics_dashboard"].passed is False
    assert checks["golden_replay_live"].passed is False
    assert "differs" in checks["golden_replay_live"].evidence
    assert checks["guardrail_tests"].passed is False
    assert checks["survives_restart"].passed is False
    assert checks["budget_alarm"].passed is False
    assert checks["owner_login"].passed is True
    assert checks["scene_text_firewall"].passed is True


def test_what_cannot_be_observed_stays_unmeasured(tmp_path: Path) -> None:
    down: httpx.Response | Exception = httpx.ConnectError("down")
    http = site(**{
        f"{API}/evidence/days": httpx.Response(200, json=[]),
        f"{API}/metrics": down,
        f"{API}/runtime/metrics": httpx.Response(503),
        f"{API}/auth/me": down,
        f"{UI}/demo": down,
    })  # fmt: skip
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    checks = run_auto_checks(
        config(tmp_path, restart_evidence=tmp_path / "bad.json", deploy_dry_run=None),
        http=http,
        run=gh(found=False),
    )
    for key in (
        "guardrail_tests",
        "evidence_routes_private",
        "survives_restart",
        "budget_alarm",
        "metrics_dashboard",
        "scene_text_firewall",
        "one_command_deploy",
        "golden_replay_live",
        "owner_login",
    ):
        assert checks[key].passed is None, key
        assert checks[key].evidence, key
    assert "not run" in checks["budget_alarm"].evidence
    assert "unreadable" in checks["survives_restart"].evidence


def test_a_failing_dry_run_and_missing_golden_chain(tmp_path: Path) -> None:
    def failing(argv: Sequence[str]) -> tuple[int, str]:
        return (2, "error: no image\n") if argv[0] == "bash" else gh()(argv)

    cfg = config(tmp_path)
    cfg.golden_chain = tmp_path / "missing.jsonl"
    checks = run_auto_checks(cfg, http=site(), run=failing)
    assert checks["one_command_deploy"].passed is False
    assert "exit 2" in checks["one_command_deploy"].evidence
    assert checks["golden_replay_live"].passed is None


def test_no_guardrails_job_and_unparsable_jobs() -> None:
    def no_job(argv: Sequence[str]) -> tuple[int, str]:
        return (0, "41") if argv[2].endswith("per_page=1") else (0, "not json")

    check = auto.guardrail_tests(AutoConfig(api=API, ui=UI), no_job)
    assert check.passed is None
    assert "no `guardrails` job" in check.evidence


def test_merge_keeps_manual_checks_and_lets_automation_win() -> None:
    manual = Checks(
        checks={"sam_agreement": Check(value=0.97, evidence="compare.py"), "owner_login": Check()}
    )
    merged = merge_checks(manual, {"owner_login": Check(passed=True, evidence="flag")})
    assert merged.checks["sam_agreement"].value == 0.97
    assert merged.checks["owner_login"].passed is True
    text = checks_yaml(merged)
    assert 'owner_login: {"passed": true, "evidence": "flag"}' in text
    assert merge_checks(None, {}).checks == {}


def test_pending_evidence_shows_on_the_scorecard() -> None:
    checks = Checks(checks={"owner_login": Check(evidence="pending the owner's passphrase")})
    card = build_scorecard(title="t", checks=checks)
    row = next(r for r in card.rows if r.target == "Owner login")
    assert row.status == "not measured"
    assert row.measured.startswith("pending: pending the owner's passphrase")


def test_run_command_captures_output() -> None:
    code, out = auto.run_command(["python3", "-c", "print('ok')"])
    assert (code, out.strip()) == (0, "ok")


def test_cli_auto_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(config: AutoConfig, **_: object) -> dict[str, Check]:
        assert config.api == API
        assert config.deploy_dry_run is None
        return {"owner_login": Check(passed=True, evidence="login_enabled=true")}

    monkeypatch.setattr(auto, "run_auto_checks", fake)
    out = tmp_path / "card" / "card.md"
    manual = tmp_path / "checks.yaml"
    manual.write_text("sam_agreement: {value: 0.96, evidence: compare.py}\n", encoding="utf-8")
    result = cli.invoke(
        app,
        ["scorecard", "--checks", "auto", "--checks-file", str(manual), "--api", API + "/",
         "--no-deploy-dry-run", "--out", str(out)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "| Owner login | yes (login_enabled=true) | PASS |" in out.read_text(encoding="utf-8")
    (evidence,) = (tmp_path / "card").glob("checks-*.yaml")
    merged = load_checks(evidence)
    assert merged.checks["sam_agreement"].value == 0.96
    monkeypatch.delenv("PUBLIC_TUNNEL_URL", raising=False)
    missing = cli.invoke(app, ["scorecard", "--checks", "auto", "--out", str(out)])
    assert missing.exit_code == 2


@pytest.mark.parametrize("script", sorted((REPO / "scripts" / "ops").glob("*.sh")))
def test_ops_scripts_parse(script: Path) -> None:
    res = subprocess.run(  # noqa: S603
        ["bash", "-n", script.relative_to(REPO).as_posix()],  # noqa: S607
        cwd=REPO,
        check=False,
    )
    assert res.returncode == 0


def test_the_committed_golden_chain_hash_is_what_the_site_must_serve() -> None:
    chain = REPO / "scenarios" / "warehouse_driver" / "chain.jsonl"
    served = REPO / "ui" / "public" / "golden" / "warehouse_driver" / "chain.jsonl"
    assert (
        hashlib.sha256(chain.read_bytes()).digest() == hashlib.sha256(served.read_bytes()).digest()
    )
