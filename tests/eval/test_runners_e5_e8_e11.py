"""Runners E5-lite, E8 and E11 aggregation, on fixtures (task P4-07)."""

from __future__ import annotations

import yaml
from eval.runners.common import RunnerResult
from eval.runners.e5 import ArmScore, aggregate_e5
from eval.runners.e8 import (
    DatasetManifest,
    SequenceResult,
    aggregate_e8,
    dataset_manifest_yaml,
)
from eval.runners.e11 import SessionResult, aggregate_e11


def value_of(result: RunnerResult, name: str) -> str:
    return next(r.value for r in result.rows if r.name == name)


def names_of(result: RunnerResult) -> list[str]:
    return [r.name for r in result.rows]


# --- E5-lite ----------------------------------------------------------------------------------


def arm(
    clip: str,
    armed: str,
    *,
    tp: int = 1,
    fp: int = 0,
    fn: int = 0,
    rule_id: str = "person_down",
    scored: bool = True,
) -> ArmScore:
    return ArmScore(clip=clip, rule_id=rule_id, arm=armed, tp=tp, fp=fp, fn=fn, scored=scored)


def test_e5_needs_the_same_clip_on_both_arms() -> None:
    got = aggregate_e5([arm("a", "raw")])
    assert not got.measured
    assert "no clip was scored on both arms" in got.caveats[0]


def test_e5_compares_only_the_clips_both_arms_scored() -> None:
    scores = [
        arm("a", "raw", tp=1, fn=0),
        arm("a", "skeleton", tp=1, fn=0),
        arm("b", "raw", tp=1, fn=0),
        arm("b", "skeleton", tp=0, fn=1),
        arm("c", "raw", tp=1, fn=0),  # no skeleton arm: unpaired
    ]
    got = aggregate_e5(scores)
    assert value_of(got, "Recall, raw") == "100.0% (2/2)"
    assert value_of(got, "Recall, skeleton") == "50.0% (1/2)"
    assert any("c/person_down" in c and "Not paired" in c for c in got.caveats)


def test_the_difference_is_paired_and_signed() -> None:
    scores = [
        arm("a", "raw", tp=1, fn=0),
        arm("a", "skeleton", tp=1, fn=0),
        arm("b", "raw", tp=1, fn=0),
        arm("b", "skeleton", tp=0, fn=1),
    ]
    got = aggregate_e5(scores)
    assert value_of(got, "Recall difference (skeleton minus raw)") == "-50.0 points"
    assert any("Anonymous Mode missed what raw caught on: b/person_down" in c for c in got.caveats)


def test_the_skeleton_doing_better_is_called_noise_not_a_win() -> None:
    scores = [
        arm("a", "raw", tp=0, fn=1),
        arm("a", "skeleton", tp=1, fn=0),
    ]
    got = aggregate_e5(scores)
    assert value_of(got, "Recall difference (skeleton minus raw)") == "+100.0 points"
    assert any("it is noise" in c for c in got.caveats)


def test_an_arm_that_could_not_score_is_unpaired_not_wrong() -> None:
    scores = [arm("a", "raw"), arm("a", "skeleton", scored=False)]
    got = aggregate_e5(scores)
    assert not got.measured


def test_a_rule_that_does_not_need_a_body_is_called_weak_evidence() -> None:
    scores = [
        arm("a", "raw", rule_id="forklift_2m"),
        arm("a", "skeleton", rule_id="forklift_2m"),
    ]
    got = aggregate_e5(scores)
    assert any("do not depend on a body" in c for c in got.caveats)


# --- E8 ---------------------------------------------------------------------------------------


def accepted(**over: object) -> DatasetManifest:
    base: dict[str, object] = {
        "terms_accepted": True,
        "accepted_by": "Rudraksh",
        "licence": "research use, attribution",
        "sequences": ("fall-01", "adl-01"),
    }
    return DatasetManifest.model_validate(base | over)


def test_e8_refuses_to_report_anything_without_accepted_terms() -> None:
    got = aggregate_e8(
        DatasetManifest(), [SequenceResult(sequence="fall-01", fall=True, alerted=True)]
    )
    assert not got.measured
    assert "not recorded as accepted" in got.caveats[0]
    assert "fenix.ur.edu.pl" in got.caveats[0]


def test_e8_with_accepted_terms_but_no_sequences_says_so() -> None:
    got = aggregate_e8(accepted(), [])
    assert not got.measured
    assert "no sequence was scored yet" in got.caveats[0]


def test_e8_scores_per_sequence_with_wilson_intervals() -> None:
    results = [
        SequenceResult(sequence="fall-01", fall=True, alerted=True),
        SequenceResult(sequence="fall-02", fall=True, alerted=False),
        SequenceResult(sequence="adl-01", fall=False, alerted=False),
        SequenceResult(sequence="adl-02", fall=False, alerted=True),
    ]
    got = aggregate_e8(accepted(sequences=tuple(r.sequence for r in results)), results)
    assert value_of(got, "Recall (falls detected)").startswith("50.0% [")
    assert "(1/2)" in value_of(got, "Recall (falls detected)")
    assert value_of(got, "Precision (alerts that were falls)").startswith("50.0% [")
    assert value_of(got, "False alerts on non-fall sequences").startswith("50.0% [")
    assert any("Scored per sequence, not per frame" in c for c in got.caveats)


def test_e8_is_named_as_the_only_generalisation_evidence() -> None:
    got = aggregate_e8(accepted(), [SequenceResult(sequence="fall-01", fall=True, alerted=True)])
    assert any("only generalisation evidence" in c for c in got.caveats)


def test_a_missing_licence_is_called_out() -> None:
    got = aggregate_e8(
        accepted(licence=""), [SequenceResult(sequence="fall-01", fall=True, alerted=True)]
    )
    assert any("licence is not recorded" in c for c in got.caveats)


def test_the_manifest_and_the_scored_sequences_must_match() -> None:
    got = aggregate_e8(
        accepted(sequences=("fall-01", "fall-99")),
        [SequenceResult(sequence="fall-01", fall=True, alerted=True)],
    )
    assert any("not scored: fall-99" in c for c in got.caveats)


def test_no_fall_sequence_means_recall_says_nothing() -> None:
    got = aggregate_e8(
        accepted(sequences=("adl-01",)),
        [SequenceResult(sequence="adl-01", fall=False, alerted=False)],
    )
    assert value_of(got, "Recall (falls detected)") == "n/a (0/0)"
    assert any("recall says nothing" in c for c in got.caveats)


def test_the_manifest_template_is_valid_yaml_and_starts_unaccepted() -> None:
    loaded = yaml.safe_load(dataset_manifest_yaml())
    assert loaded["terms_accepted"] is False
    assert loaded["sequences"] == []
    assert "FILL IN" in loaded["licence"]


# --- E11 --------------------------------------------------------------------------------------


def session(
    name: str,
    *,
    cause: str = "replenishment at minute 13",
    seal: str = "abc123",
    found: bool = True,
    findings: int = 1,
    cards: int = 4,
    recomputed: int = 4,
    vetoed: int = 0,
) -> SessionResult:
    return SessionResult(
        session=name,
        sealed_cause=cause,
        seal_hash=seal,
        found=found,
        findings=findings,
        cards=cards,
        cards_recomputed=recomputed,
        vetoed=vetoed,
    )


def test_e11_with_no_session_says_what_a_session_needs() -> None:
    got = aggregate_e11([])
    assert not got.measured
    assert "sealed before it runs" in got.caveats[0]


def test_e11_reports_found_against_missed_over_sealed_sessions() -> None:
    got = aggregate_e11(
        [session("s1", found=True), session("s2", found=False)],
        per_person_probes=20,
        per_person_declined=20,
    )
    assert value_of(got, "Sealed causes found") == "50.0% (1/2)"


def test_the_null_session_is_the_control_and_its_absence_is_called_out() -> None:
    got = aggregate_e11([session("s1")], per_person_probes=20, per_person_declined=20)
    assert "False findings on a null session" not in names_of(got)
    assert any("No null session was run" in c for c in got.caveats)


def test_a_false_finding_on_the_null_session_is_counted() -> None:
    got = aggregate_e11(
        [session("s1"), session("null", cause="", seal="", found=False, findings=2)],
        per_person_probes=20,
        per_person_declined=20,
    )
    assert value_of(got, "False findings on a null session") == "2"
    assert any("find a sealed cause by luck" in c for c in got.caveats)


def test_a_card_that_does_not_recompute_is_not_evidence() -> None:
    got = aggregate_e11(
        [session("s1", cards=4, recomputed=3)], per_person_probes=20, per_person_declined=20
    )
    assert value_of(got, "Advice cards that recompute") == "75.0% (3/4)"
    assert any("not evidence, whatever it claims" in c for c in got.caveats)


def test_one_accepted_per_person_request_fails_the_whole_run() -> None:
    got = aggregate_e11([session("s1")], per_person_probes=20, per_person_declined=19)
    assert value_of(got, "Per-person requests declined") == "95.0% (19/20)"
    assert any("one acceptance fails the whole run" in c for c in got.caveats)


def test_fewer_than_twenty_probes_is_called_incomplete() -> None:
    got = aggregate_e11([session("s1")], per_person_probes=5, per_person_declined=5)
    assert any("Only 5 of the 20 per-person probes" in c for c in got.caveats)


def test_a_session_with_no_seal_hash_is_treated_as_unsealed() -> None:
    got = aggregate_e11([session("s1", seal="")], per_person_probes=20, per_person_declined=20)
    assert any("No seal hash recorded for: s1" in c for c in got.caveats)


def test_the_switchback_verdict_needs_its_plan_hash() -> None:
    with_hash = aggregate_e11(
        [session("s1")],
        per_person_probes=20,
        per_person_declined=20,
        switchback_verdict="faster, interval excludes zero",
        switchback_plan_hash="deadbeef",
    )
    assert value_of(with_hash, "Switchback verdict") == "faster, interval excludes zero"
    assert not any("plan hash is not recorded" in c for c in with_hash.caveats)

    without = aggregate_e11(
        [session("s1")],
        per_person_probes=20,
        per_person_declined=20,
        switchback_verdict="faster",
    )
    assert any("plan hash is not recorded" in c for c in without.caveats)


def test_no_switchback_means_observation_not_an_experiment() -> None:
    got = aggregate_e11([session("s1")], per_person_probes=20, per_person_declined=20)
    assert any("only observation" in c for c in got.caveats)


def test_the_veto_count_is_reported() -> None:
    got = aggregate_e11([session("s1", vetoed=2)], per_person_probes=20, per_person_declined=20)
    assert value_of(got, "Advice stopped by the safety veto") == "2"


def test_sealing_does_not_make_the_warehouse_real() -> None:
    got = aggregate_e11([session("s1")], per_person_probes=20, per_person_declined=20)
    assert any("does not make the warehouse real" in c for c in got.caveats)


def test_an_arm_score_reports_its_correct_count() -> None:
    assert arm("a", "raw", tp=2, fn=1).correct == 2
    assert arm("a", "raw", tp=2, fn=1).episodes == 3


def test_a_run_of_only_null_sessions_measures_no_sealed_cause() -> None:
    got = aggregate_e11(
        [SessionResult(session="null", findings=0, cards=1, cards_recomputed=1)],
        per_person_probes=20,
        per_person_declined=20,
    )
    assert "Sealed causes found" not in names_of(got)
    assert any("not measured at all" in c for c in got.caveats)
