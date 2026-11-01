"""The E1 runner: Wilson intervals, ablations, baselines and run stability (task P4-06)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from eval.runners.e1 import (
    ALL_ARMS,
    REQUIRED_RUNS,
    Arm,
    ArmOutcome,
    aggregate_e1,
    rate,
    run_e1_evaluation,
)

from rulecam.eval.manifests import E1Manifest, ManifestEntry
from rulecam.stats.intervals import wilson_interval

ZERO = "0" * 64


def made_entry(
    clip: str,
    *,
    rule_id: str = "forklift_2m",
    label: str = "positive",
    use_case: str = "uc1",
    staged_by: str = "team",
) -> ManifestEntry:
    return ManifestEntry.model_validate(
        {
            "path": f"eval/clips/{clip}/clip.mp4",
            "sha256": ZERO,
            "use_case": use_case,
            "rule_id": rule_id,
            "label": label,
            "staged_by": staged_by,
            "provenance": "recorded",
            "consent": True,
            "consent_form": "form-001",
        }
    )


def manifest_of(*entries: ManifestEntry) -> E1Manifest:
    return E1Manifest(name="e1-test", entries=entries)


SIMPLE = manifest_of(
    made_entry("pos_a"),
    made_entry("pos_b"),
    made_entry("neg_a", label="negative"),
)


class FixedRunner:
    """A deterministic fake pipeline: every clip and run gives the same counts."""

    def __init__(self, counts: dict[str, tuple[int, int, int, int]], seconds: float = 60.0) -> None:
        self.counts = counts
        self.seconds = seconds
        self.calls: list[tuple[Arm, int]] = []

    def run(self, arm: Arm, entries: Sequence[ManifestEntry], run_idx: int) -> Sequence[ArmOutcome]:
        self.calls.append((arm, run_idx))
        out = []
        for e in entries:
            tp, fp, fn, alerts = self.counts[e.clip_name]
            out.append(
                ArmOutcome(
                    arm=arm,
                    run_idx=run_idx,
                    clip=e.clip_name,
                    rule_id=e.rule_id,
                    tp=tp,
                    fp=fp,
                    fn=fn,
                    alerts=alerts,
                    clip_seconds=self.seconds,
                )
            )
        return out


class DriftingRunner(FixedRunner):
    """A pipeline that answers differently on the third run: not deterministic."""

    def run(self, arm: Arm, entries: Sequence[ManifestEntry], run_idx: int) -> Sequence[ArmOutcome]:
        rows = list(super().run(arm, entries, run_idx))
        if run_idx == 2:
            rows[0] = rows[0].model_copy(update={"tp": rows[0].tp + 1})
        return rows


def test_rate_matches_the_shared_wilson_function() -> None:
    got = rate(7, 10)
    assert got.value == pytest.approx(0.7)
    assert (got.lo, got.hi) == wilson_interval(7, 10, 0.95)


def test_no_trials_gives_no_value_and_the_full_interval() -> None:
    got = rate(0, 0)
    assert got.value is None
    assert not got.known
    assert (got.lo, got.hi) == (0.0, 1.0)


def test_every_arm_runs_the_set_five_times() -> None:
    runner = FixedRunner({"pos_a": (1, 0, 0, 1), "pos_b": (1, 0, 0, 1), "neg_a": (0, 0, 0, 0)})
    summary = run_e1_evaluation(SIMPLE, runner)
    assert summary.runs_per_arm == REQUIRED_RUNS
    assert summary.arms == ALL_ARMS
    assert len(runner.calls) == len(ALL_ARMS) * REQUIRED_RUNS
    assert sorted({c[1] for c in runner.calls}) == [0, 1, 2, 3, 4]


def test_repeats_are_not_summed_into_the_counts() -> None:
    """Five runs of two positive clips are 2 labelled episodes, not 10."""
    runner = FixedRunner({"pos_a": (1, 0, 0, 1), "pos_b": (1, 0, 0, 1), "neg_a": (0, 0, 0, 0)})
    summary = run_e1_evaluation(SIMPLE, runner, arms=["predicates"])
    row = summary.score("predicates", "all", "all")
    assert row is not None
    assert (row.tp, row.fp, row.fn) == (2, 0, 0)
    assert row.clips == 3
    assert row.runs == REQUIRED_RUNS
    assert row.recall.total == 2


def test_precision_and_recall_carry_their_wilson_interval() -> None:
    runner = FixedRunner({"pos_a": (1, 0, 0, 1), "pos_b": (0, 0, 1, 0), "neg_a": (0, 1, 0, 1)})
    summary = run_e1_evaluation(SIMPLE, runner, arms=["glimmer"])
    row = summary.score("glimmer", "all", "all")
    assert row is not None
    assert row.precision.value == pytest.approx(0.5)  # 1 tp, 1 fp
    assert row.recall.value == pytest.approx(0.5)  # 1 tp, 1 fn
    assert row.precision.lo < 0.5 < row.precision.hi
    assert (row.recall.lo, row.recall.hi) == wilson_interval(1, 2, 0.95)


def test_false_alerts_per_hour_uses_the_negative_clips_only() -> None:
    runner = FixedRunner(
        {"pos_a": (1, 0, 0, 9), "pos_b": (1, 0, 0, 9), "neg_a": (0, 2, 0, 2)}, seconds=120.0
    )
    summary = run_e1_evaluation(SIMPLE, runner, arms=["predicates"])
    row = summary.score("predicates", "all", "all")
    assert row is not None
    assert row.false_alerts == 2  # the positives' 18 alerts are not false
    assert row.negative_seconds == pytest.approx(120.0)
    assert row.false_alerts_per_hour == pytest.approx(60.0)  # 2 alerts in 120 s


def test_no_negative_clip_means_no_false_alert_rate() -> None:
    only_positive = manifest_of(made_entry("pos_a"))
    runner = FixedRunner({"pos_a": (1, 0, 0, 1)})
    summary = run_e1_evaluation(only_positive, runner, arms=["predicates"])
    row = summary.score("predicates", "all", "all")
    assert row is not None
    assert row.false_alerts_per_hour is None


def test_a_deterministic_runner_is_stable_and_repeats_give_the_same_summary() -> None:
    runner = FixedRunner({"pos_a": (1, 0, 0, 1), "pos_b": (1, 0, 0, 1), "neg_a": (0, 0, 0, 0)})
    first = run_e1_evaluation(SIMPLE, runner, arms=["predicates"])
    second = run_e1_evaluation(SIMPLE, runner, arms=["predicates"])
    assert first == second
    row = first.score("predicates", "all", "all")
    assert row is not None
    assert row.stable


def test_a_drifting_runner_is_reported_as_not_stable() -> None:
    runner = DriftingRunner({"pos_a": (1, 0, 0, 1), "pos_b": (1, 0, 0, 1), "neg_a": (0, 0, 0, 0)})
    summary = run_e1_evaluation(SIMPLE, runner, arms=["predicates"])
    row = summary.score("predicates", "all", "all")
    assert row is not None
    assert not row.stable
    assert any("not deterministic" in w for w in summary.warnings)


def test_slices_are_reported_per_rule_use_case_and_stager() -> None:
    mixed = manifest_of(
        made_entry("pos_a", rule_id="forklift_2m", use_case="uc1"),
        made_entry("pos_b", rule_id="helmet_required", use_case="uc2", staged_by="outsider"),
    )
    runner = FixedRunner({"pos_a": (1, 0, 0, 1), "pos_b": (0, 0, 1, 0)})
    summary = run_e1_evaluation(mixed, runner, arms=["spark_open"])

    forklift = summary.score("spark_open", "rule", "forklift_2m")
    helmet = summary.score("spark_open", "rule", "helmet_required")
    assert forklift is not None
    assert helmet is not None
    assert forklift.recall.value == pytest.approx(1.0)
    assert helmet.recall.value == pytest.approx(0.0)

    uc2 = summary.score("spark_open", "use_case", "uc2")
    assert uc2 is not None
    assert uc2.clips == 1

    outsider = summary.score("spark_open", "staged_by", "outsider")
    team = summary.score("spark_open", "staged_by", "team")
    assert outsider is not None
    assert team is not None
    assert outsider.recall.value == pytest.approx(0.0)
    assert team.recall.value == pytest.approx(1.0)


def test_a_team_only_manifest_is_warned_about() -> None:
    runner = FixedRunner({"pos_a": (1, 0, 0, 1), "pos_b": (1, 0, 0, 1), "neg_a": (0, 0, 0, 0)})
    summary = run_e1_evaluation(SIMPLE, runner, arms=["predicates"])
    assert any("no generalisation" in w for w in summary.warnings)


def test_fewer_runs_than_asked_for_is_warned_about() -> None:
    """One run of five establishes nothing about stability, and the summary must say so."""
    partial = aggregate_e1(
        SIMPLE,
        [ArmOutcome(arm="predicates", run_idx=0, clip="pos_a", rule_id="forklift_2m", tp=1)],
        runs_per_arm=REQUIRED_RUNS,
    )
    assert any("1 of 5 runs present" in w for w in partial.warnings)


def test_all_the_runs_asked_for_raises_no_run_count_warning() -> None:
    runner = FixedRunner({"pos_a": (1, 0, 0, 1), "pos_b": (1, 0, 0, 1), "neg_a": (0, 0, 0, 0)})
    summary = run_e1_evaluation(SIMPLE, runner, arms=["predicates"], runs_per_arm=2)
    assert not any("runs present" in w for w in summary.warnings)


def test_a_clip_that_could_not_be_scored_is_listed_not_counted() -> None:
    outcomes = [
        ArmOutcome(arm="predicates", run_idx=0, clip="pos_a", rule_id="forklift_2m", tp=1),
        ArmOutcome(
            arm="predicates",
            run_idx=0,
            clip="pos_b",
            rule_id="forklift_2m",
            error="pos_b: the plan was refused",
        ),
    ]
    summary = aggregate_e1(SIMPLE, outcomes, runs_per_arm=1)
    row = summary.score("predicates", "all", "all")
    assert row is not None
    assert row.clips == 1
    assert row.tp == 1
    assert row.errors == ("pos_b: the plan was refused",)


def test_a_summary_is_simulated_unless_the_caller_says_otherwise() -> None:
    """No real runner exists yet, so forgetting the flag must never pass as a measured run."""
    runner = FixedRunner({"pos_a": (1, 0, 0, 1), "pos_b": (1, 0, 0, 1), "neg_a": (0, 0, 0, 0)})
    assert run_e1_evaluation(SIMPLE, runner, arms=["frame_vlm"]).simulated
    assert aggregate_e1(SIMPLE, []).simulated
    assert not run_e1_evaluation(SIMPLE, runner, arms=["frame_vlm"], simulated=False).simulated


def test_an_errored_run_does_not_zero_the_negative_duration() -> None:
    """Durations come from each arm's scored run, not from whichever outcome came first."""
    neg = {"clip": "neg_a", "rule_id": "forklift_2m"}
    outcomes = [
        ArmOutcome(arm="glimmer", run_idx=0, error="neg_a: decode failed", **neg),
        ArmOutcome(arm="predicates", run_idx=0, alerts=2, clip_seconds=1800.0, **neg),
        ArmOutcome(arm="glimmer", run_idx=1, alerts=1, clip_seconds=1800.0, **neg),
    ]
    summary = aggregate_e1(SIMPLE, outcomes, runs_per_arm=1)
    expected: tuple[tuple[Arm, float], ...] = (("predicates", 4.0), ("glimmer", 2.0))
    for arm, per_hour in expected:
        row = summary.score(arm, "all", "all")
        assert row is not None
        assert row.negative_seconds == pytest.approx(1800.0)
        assert row.false_alerts_per_hour == pytest.approx(per_hour)


def test_outcomes_outside_the_manifest_are_not_counted() -> None:
    """The manifest is the consent gate, so it must also gate the aggregation."""
    outcomes = [
        ArmOutcome(arm="predicates", run_idx=0, clip="pos_a", rule_id="forklift_2m", tp=1),
        ArmOutcome(arm="predicates", run_idx=0, clip="no_consent", rule_id="forklift_2m", tp=1),
        ArmOutcome(arm="predicates", run_idx=0, clip="pos_a", rule_id="helmet_required", fp=3),
        ArmOutcome(arm="frame_vlm", run_idx=0, clip="stray", rule_id="forklift_2m", fp=1),
    ]
    summary = aggregate_e1(SIMPLE, outcomes, runs_per_arm=1)
    assert summary.arms == ("predicates",)
    row = summary.score("predicates", "all", "all")
    assert row is not None
    assert (row.clips, row.tp, row.fp) == (1, 1, 0)
    assert [r.slice_id for r in summary.by_rule] == ["forklift_2m"]
    assert [r.slice_id for r in summary.by_use_case] == ["uc1"]
    assert summary.by_use_case[0].clips == 1
    assert any("3 outcome(s)" in w for w in summary.warnings)


def test_a_use_case_with_no_outcomes_gets_no_row() -> None:
    manifest = manifest_of(made_entry("pos_a", use_case="uc3"))
    outcomes = [ArmOutcome(arm="predicates", run_idx=0, clip="pos_a", rule_id="forklift_2m", tp=1)]
    summary = aggregate_e1(manifest, outcomes, runs_per_arm=1)
    assert [r.slice_id for r in summary.by_use_case] == ["uc3"]


def test_the_headline_is_the_full_arm_and_is_none_without_it() -> None:
    runner = FixedRunner(
        {"pos_a": (1, 0, 0, 1), "pos_b": (1, 0, 1, 1), "neg_a": (0, 0, 0, 1)}, seconds=3600.0
    )
    full = run_e1_evaluation(SIMPLE, runner, arms=["spark_open"])
    assert full.headline()["recall"] == pytest.approx(2 / 3)
    assert full.headline()["false_alerts_per_hour"] == pytest.approx(1.0)

    predicates_only = run_e1_evaluation(SIMPLE, runner, arms=["predicates"])
    assert predicates_only.headline() == {
        "recall": None,
        "precision": None,
        "false_alerts_per_hour": None,
    }


def test_an_unmeasured_slice_is_none() -> None:
    runner = FixedRunner({"pos_a": (1, 0, 0, 1), "pos_b": (1, 0, 0, 1), "neg_a": (0, 0, 0, 0)})
    summary = run_e1_evaluation(SIMPLE, runner, arms=["predicates"])
    assert summary.score("yolov8_ppe", "all", "all") is None
    assert summary.score("predicates", "rule", "no_such_rule") is None
