"""The PLAN §2 scorecard: criteria from a report, checks and live samples, and its markdown."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.fakes import EPOCH

from rulecam.eval.errors import EvalError
from rulecam.eval.score import EvalReport, RuleScore, RunScore, ScoreConfig
from rulecam.eval.scorecard import (
    Check,
    Checks,
    LiveSample,
    Row,
    Scorecard,
    build_scorecard,
    load_checks,
    render_markdown,
)


def run(**over: Any) -> RunScore:
    data: dict[str, Any] = {
        "clip": "yard",
        "rule": "forklift_2m",
        "status": "labelled",
        "expect": "activatable",
        "outcome": "activated",
        "speed": 1.0,
        "request": "Keep people 2 m from a moving forklift.",
        "rule_hashes": ("h1",),
        "candidates": 10,
        "alerts": 9,
        "events": 10,
        "tp": 9,
        "fp": 0,
        "fn": 1,
        "latencies_s": (4.0, 6.0, 9.0),
        "tier1": ("violation",) * 9 + ("unsure",),
        "decisive": 9,
        "agree": 9,
        "lags_s": (1.0, 2.0, 3.0),
        "billed_frames": 500,
        "camera_hours": 1.0,
    }
    data.update(over)
    return RunScore.model_validate(data)


def rule(**over: Any) -> RuleScore:
    data: dict[str, Any] = {
        "rule": "forklift_2m",
        "clips": 2,
        "tp": 9,
        "fp": 0,
        "fn": 1,
        "precision": 1.0,
        "recall": 0.9,
        "false_alerts_on_negatives": 0,
        "negative_clips": 1,
        "latency_p95_s": 9.0,
    }
    data.update(over)
    return RuleScore.model_validate(data)


def report(runs: list[RunScore], rules: list[RuleScore], **config: float) -> EvalReport:
    return EvalReport(
        generated_at=EPOCH, config=ScoreConfig(**config), runs=tuple(runs), rules=tuple(rules)
    )


def rows(card: Scorecard, area: str) -> list[Row]:
    return [r for r in card.rows if r.area == area]


def by_target(card: Scorecard, text: str) -> Row:
    (found,) = [r for r in card.rows if text in r.target]
    return found


def test_an_empty_scorecard_measures_nothing() -> None:
    card = build_scorecard(title="t")
    assert {r.status for r in card.rows} == {"not measured"}
    assert len(card.areas) == 8
    assert card.area_status("Alert quality") == "not measured"


def test_alert_quality_passes_and_fails_per_rule() -> None:
    negative = run(clip="neg", status="negative", events=0, tp=0, fn=0, alerts=0, candidates=2)
    good = build_scorecard(
        title="t", report=report([run(), negative], [rule()], gpu_usd_per_hour=0.0)
    )
    assert by_target(good, "Precision").status == "pass"
    assert by_target(good, "Recall").status == "pass"
    assert by_target(good, "0 false alerts").status == "pass"
    assert "2 unverified candidates" in by_target(good, "0 false alerts").measured
    assert by_target(good, "decisive").status == "pass"  # 18 of 20
    assert good.area_status("Alert quality") == "pass"
    bad = build_scorecard(
        title="t",
        report=report(
            [run(decisive=1, agree=0)],
            [rule(recall=0.5), rule(rule="wash", precision=0.5, false_alerts_on_negatives=2)],
        ),
    )
    assert by_target(bad, "Precision").status == "fail"
    assert "wash 0.50" in by_target(bad, "Precision").measured
    assert by_target(bad, "Recall").status == "fail"
    assert by_target(bad, "0 false alerts").status == "fail"
    assert by_target(bad, "decisive").status == "fail"
    assert by_target(bad, "matches the label").status == "fail"
    assert bad.area_status("Alert quality") == "fail"


def test_no_alerts_yet_is_not_a_pass() -> None:
    card = build_scorecard(title="t", report=report([], [rule(precision=None, recall=None)]))
    assert by_target(card, "Precision").measured == "n/a (no alerts yet)"
    assert by_target(card, "Recall").measured == "n/a (no labelled positives yet)"


def test_real_time_from_runs_and_from_live_samples() -> None:
    slow = run(speed=2.0, lags_s=(100.0,), keyframe_mode=True, latencies_s=(50.0,))
    card = build_scorecard(title="t", report=report([run(), slow], [rule()]))
    assert by_target(card, "p95 lag").status == "pass"  # the 2x run is ignored
    assert by_target(card, "alert latency").status == "pass"
    assert by_target(card, "Keyframe").status == "pass"
    live = LiveSample(lags_s=(120.0, 150.0), keyframe_mode=True, source="live pod")
    card = build_scorecard(title="t", report=report([run()], [rule()]), live=live)
    assert by_target(card, "p95 lag").status == "fail"
    assert "live pod" in by_target(card, "p95 lag").measured
    assert by_target(card, "Keyframe").status == "fail"


def test_cost_idle_and_active() -> None:
    idle = run(clip="neg", status="negative", events=0, billed_frames=300, camera_hours=1.0)
    active = run(billed_frames=5000, camera_hours=1.0)  # $1.00 per camera-hour
    card = build_scorecard(title="t", report=report([idle, active], [rule()]))
    assert by_target(card, "idle").status == "pass"
    assert "$0.060" in by_target(card, "idle").measured
    assert "GPU not priced" in by_target(card, "idle").measured
    assert by_target(card, "active").status == "fail"
    unmetered = build_scorecard(title="t", report=report([run(billed_frames=None)], [rule()]))
    assert by_target(unmetered, "active").status == "not measured"
    gpu = build_scorecard(title="t", report=report([idle], [rule()], gpu_usd_per_hour=0.8))
    assert by_target(gpu, "idle").status == "fail"
    assert by_target(gpu, "active").status == "not measured"


def test_tracking_rows() -> None:
    tracking = (
        {
            "concept": "person",
            "frames": 300,
            "hota": 0.7,
            "idf1": 0.8,
            "id_switches": 1,
            "id_switches_per_min": 0.5,
        },
        {
            "concept": "forklift",
            "frames": 300,
            "hota": 0.55,
            "idf1": 0.9,
            "id_switches": 0,
            "id_switches_per_min": 0.0,
        },
    )
    card = build_scorecard(title="t", report=report([run(tracking=tracking)], [rule()]))
    assert by_target(card, "ID switches").status == "pass"
    assert by_target(card, "HOTA").status == "fail"
    assert "0.55" in by_target(card, "HOTA").measured


def test_planner_determinism_and_outcomes() -> None:
    same = [run(rule_hashes=("h1",)) for _ in range(20)]
    card = build_scorecard(title="t", report=report(same, [rule()]))
    assert by_target(card, "canonical rule").status == "pass"
    few = build_scorecard(title="t", report=report(same[:3], [rule()]))
    assert by_target(few, "canonical rule").status == "not measured"
    assert "3/3" in by_target(few, "canonical rule").measured
    split = build_scorecard(title="t", report=report([run(), run(rule_hashes=("h2",))], [rule()]))
    assert by_target(split, "canonical rule").status == "fail"
    outcomes = [
        run(),
        run(expect="refusal", outcome="refused", refused="no"),
        run(expect="activatable", outcome="error", error="timeout"),
    ]
    planner = build_scorecard(title="t", report=report(outcomes, [rule()]))
    row = by_target(planner, "honest refusals")
    assert row.status == "fail"
    assert "2/3" in row.measured


def test_checks_fill_external_criteria(tmp_path: Path) -> None:
    checks = Checks(
        checks={
            "owner_login": Check(passed=True, evidence="PR #120"),
            "survives_restart": Check(passed=False),
            "sam_agreement": Check(value=0.97),
            "planner_determinism": Check(passed=True, evidence="20/20 script"),
        }
    )
    card = build_scorecard(title="t", checks=checks)
    assert by_target(card, "Owner login").status == "pass"
    assert by_target(card, "Owner login").measured == "yes (PR #120)"
    assert by_target(card, "pod restart").status == "fail"
    assert by_target(card, "Detection agreement").status == "pass"
    assert by_target(card, "canonical rule").status == "pass"
    assert by_target(card, "Guardrail").status == "not measured"
    path = tmp_path / "checks.yaml"
    path.write_text("owner_login: {passed: true}\nsurvives_restart:\n", encoding="utf-8")
    loaded = load_checks(path)
    assert loaded.checks["owner_login"].passed
    assert loaded.checks["survives_restart"].passed is None
    path.write_text("- a list\n", encoding="utf-8")
    with pytest.raises(EvalError):
        load_checks(path)


def test_render_markdown() -> None:
    refused = run(clip="neg", outcome="refused", refused="no rule | passes", status="negative")
    card = build_scorecard(
        title="Rulecam 9.5 scorecard, 2026-10-31",
        report=report([run(), refused], [rule()]),
        checks=Checks(checks={"owner_login": Check(passed=True)}),
        sources=("replayed runs: eval/baseline",),
    )
    text = render_markdown(card)
    assert text.startswith("# Rulecam 9.5 scorecard, 2026-10-31\n")
    assert "- replayed runs: eval/baseline" in text
    assert "| Alert quality | 9.5 | PASS |" in text
    assert "| Safety and privacy | 9.5 | NOT MEASURED |" in text
    assert "| Precision ≥ 0.92 per rule | forklift_2m 1.00 | PASS |" in text
    assert "refused: no rule \\| passes" in text
    assert "## Per rule" in text
    assert "## Per run" in text
    assert "temporal IoU ≥ 0.3" in text
    empty = render_markdown(build_scorecard(title="t"))
    assert "## Per run" not in empty
