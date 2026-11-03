"""Add-on 1.2: golden-run score gates (`rulecam golden gate`) and the pending T02/T04 slots."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from rulecam.cli import app
from rulecam.demo.gate import check_gate, gate_all, gate_scenario
from rulecam.eval.schema import load_eval_set
from rulecam.replay.golden import load_scenario

REPO = Path(__file__).resolve().parents[2]
SCENARIOS = REPO / "scenarios"
cli = CliRunner()


@pytest.fixture(autouse=True)
def _repo_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO)  # scenario params name repo-relative fixture folders


SCORE = {
    "alerts": 1,
    "false_alerts_on_negative": 0,
    "tp": 1,
    "fp": 0,
    "tier1": ["violation"],
    "outcome": "activated",
}


def test_check_gate() -> None:
    assert check_gate(SCORE, {"min_alerts": 1, "tier1_includes": "violation"}) == []
    assert check_gate(SCORE, {"max_alerts": 0}) == ["alerts 1 > 0"]
    assert check_gate(SCORE, {"min_tp": 2, "max_fp": 0}) == ["tp 1 < 2"]
    assert check_gate(SCORE, {"tier1_includes": "no_violation"}) == [
        "no Tier 1 verdict 'no_violation' (got ['violation'])"
    ]
    assert check_gate(SCORE, {"outcome": "refused"}) == ["outcome 'activated' != 'refused'"]
    assert check_gate(SCORE, {"max_false_alerts_on_negative": 0}) == []
    assert check_gate(SCORE, {"min_recall": 1}) == ["unknown gate key 'min_recall'"]


def test_the_committed_goldens_pass_their_gates() -> None:
    results = {r.scenario: r for r in gate_all(SCENARIOS)}
    assert sorted(results) == ["driver_only_4m", "warehouse_driver", "worker_passes_4m"]
    assert all(r.ok for r in results.values()), [r.failures for r in results.values()]
    assert results["warehouse_driver"].score["alerts"] == 0
    assert results["driver_only_4m"].score["alerts"] == 0
    alerts = results["worker_passes_4m"].score["alerts"]
    assert isinstance(alerts, int)
    assert alerts >= 1


def test_a_broken_gate_fails(tmp_path: Path) -> None:
    data = yaml.safe_load((SCENARIOS / "warehouse_driver.yaml").read_text(encoding="utf-8"))
    data["steps"][-1]["params"]["gate"] = {"min_alerts": 1}
    path = tmp_path / "warehouse_driver.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = gate_scenario(path)
    assert not result.ok
    assert result.failures == ("alerts 0 < 1",)
    invoked = cli.invoke(app, ["golden", "gate", "--scenarios-dir", str(tmp_path)])
    assert invoked.exit_code == 1
    assert "✗ warehouse_driver" in invoked.output


def test_a_scenario_without_a_score_step_passes(tmp_path: Path) -> None:
    path = tmp_path / "x.yaml"
    path.write_text("name: x\nsteps:\n  - {id: a, kind: demo.plan}\n", encoding="utf-8")
    assert gate_scenario(path).ok


def test_cli_gate(tmp_path: Path) -> None:
    result = cli.invoke(app, ["golden", "gate"])
    assert result.exit_code == 0, result.output
    assert "✓ warehouse_driver" in result.output
    assert cli.invoke(app, ["golden", "gate", "--scenarios-dir", str(tmp_path)]).exit_code == 2


def test_t02_t04_are_recorded_goldens() -> None:
    evalset = load_eval_set(REPO / "eval" / "clips")
    expected = {
        "worker_passes_4m": ("pexels_32243654", "violation"),
        "driver_only_4m": ("pexels_32243653", "no_violation"),
    }
    for name, (clip, verdict) in expected.items():
        scenario = load_scenario(SCENARIOS / f"{name}.yaml")
        assert scenario.name == name
        assert all(s.params["fixture"] == f"scenarios/{name}" for s in scenario.steps)
        assert (SCENARIOS / name / "run.json").is_file()
        gate = scenario.steps[-1].params["gate"]
        assert isinstance(gate, dict)
        assert gate["tier1_includes"] == verdict
        assert evalset.clip(clip).rule("forklift_4m").request.startswith("Keep people at least 4")
    ui = (REPO / "ui" / "src" / "fixtures" / "golden" / "index.ts").read_text(encoding="utf-8")
    for name in expected:
        assert f'from "./{name}.json"' in ui
        assert f'name: "{name}"' not in ui  # no longer pending
