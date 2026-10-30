from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from rulecam.contracts import (
    AcceptanceCheck,
    AcceptanceResult,
    Ack,
    ActionRecord,
    AuditFinding,
    Calibration,
    CandidateEvent,
    Change,
    Detection,
    Dispute,
    EventRecord,
    Explanation,
    FeasibilityVerdict,
    Gain,
    InventoryItem,
    Keypoint,
    Mask,
    Measurement,
    MeasurementPlan,
    PrivacyMode,
    Probe,
    Provenance,
    Recommendation,
    RequestItem,
    SafetyCheck,
    SitePlan,
    SlotClaim,
    SurpriseEvent,
    SurpriseWindow,
    Verdict,
    VerifierVerdict,
    Zone,
)
from tests.fakes import make_chunk, make_track, make_verdict

TS = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)

GAIN = Gain(
    metric="dock3_wait_for_forklift_min",
    interval=(-14.0, -4.0),
    level=0.8,
    unit="min/truck",
    model_rung="erlang_c",
    assumptions={"poisson_arrivals": "pass", "stable_service": "pass"},
)
ACCEPTANCE = AcceptanceResult(
    checks=(
        AcceptanceCheck(item="wash-before-line", check="zone_in_view", passed=True),
        AcceptanceCheck(item="wash-before-line", check="ci_green", passed=True),
    ),
    rounds=1,
)

SAMPLES: list[BaseModel] = [
    make_chunk(),
    Mask(rle="abc", box_xyxy=(0, 0, 10, 10), confidence=0.8),
    Detection(
        camera_id="cam1",
        frame_idx=3,
        ts=TS,
        concept="person",
        object_id=1,
        mask=Mask(rle="abc", box_xyxy=(0, 0, 10, 10)),
    ),
    make_track(feet_m=[(0, 0), (0.1, 0)]),
    Keypoint(x=1, y=2, score=0.9),
    Calibration(
        camera_id="cam1",
        homography=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        method="clicks4",
        err_m=0.03,
    ),
    Zone(zone_id="z", camera_id="cam1", kind="buffer", buffer_of_concept="sink", buffer_m=0.6),
    Measurement(value=1.6, lo=1.5, hi=1.7, unit="m"),
    RequestItem(kind="voice", lang="es", text="quiero pasar la próxima inspección"),
    InventoryItem(concept="sink", camera_id="cam1", count=1, mean_confidence=0.93),
    Probe(
        concept="hands",
        camera_id="cam1",
        n_frames=12,
        detect_rate=0.92,
        detect_lb=0.86,
        median_px_height=40,
    ),
    FeasibilityVerdict(
        concern="cold holding at 41F",
        verdict="not_observable",
        says="I can't see a thermometer, so I can't check cold holding.",
        alternative="point a phone at the dial thermometer, or add a sensor",
    ),
    ACCEPTANCE,
    AuditFinding(by="spark-open", text="back-door sink not covered"),
    CandidateEvent(
        event_id="e1",
        rule_id="wash-before-line",
        camera_id="cam1",
        ts_start=TS,
        ts_end=TS,
        severity="medium",
        measurement=Measurement(value=8.0, unit="s"),
        predicate_trace={"dwell_s": 8.0, "entered": True, "zone": "line_entry"},
        strip_paths=("a.jpg",),
        track_window_hash="t",
        plan_version_hash="p",
        rule_ir_hash="r",
    ),
    make_verdict(),
    ActionRecord(kind="interlock_cut", gate="auto", ts=TS, ok=True),
    Ack(role="charge_nurse", ts=TS),
    Dispute(channel="tap", reason_text="backpack strap, not a harness"),
    EventRecord(
        seq=0,
        kind="plan",
        ts=TS,
        site="kitchen",
        payload={"nested": {"list": [1, 2.5, "x", True]}},
        prev_hash="0" * 64,
        hash="h",
    ),
    SlotClaim(template="Dock 3 turnaround {m1} vs dock 1 {m2}", slots={"m1": "a"}, grade="B"),
    Explanation(id="h1", statement="13:00 replenishment takes both forklifts", status="supported"),
    Change(op="resequence", params={"task": "replenishment", "to": "before 12:00"}),
    GAIN,
    SafetyCheck(
        rules_checked=("ped-forklift-2m",),
        verdict="pass",
        considered_not_recommended=(("add a second forklift", "raises ped-forklift-2m exposure"),),
    ),
    MeasurementPlan(
        id="mp-1",
        recommendation_id="rec-1",
        primary_metric="dock3_wait",
        guard_metrics=("near_miss_per_forklift_hour",),
        design="switchback",
        block_minutes=20,
        n_pairs=6,
        min_detectable_effect=4.0,
        smallest_possible_p=0.03125,
        decision_rule="worked if CI upper < 0",
        preregistration_hash="abc",
    ),
    Verdict(plan_id="mp-1", label="worked", effect=-9, ci=(-14, -4), p_value=0.03, pairs_done=6),
    SurpriseWindow(
        camera_id="cam4",
        window_start=TS,
        score=0.4,
        z=5.1,
        tokens_kept=400,
        tokens_total=1536,
        condition="B_sam",
    ),
]


@pytest.mark.parametrize("model", SAMPLES, ids=lambda m: type(m).__name__)
def test_model_round_trips_through_json(model: BaseModel) -> None:
    assert type(model).model_validate_json(model.model_dump_json()) == model


def test_site_plan_round_trips() -> None:
    plan = SitePlan(
        plan_id="main-st-kitchen",
        site_name="Main St kitchen",
        setting_inferred="commercial_kitchen",
        languages=("es", "en"),
        request=(RequestItem(kind="text", text="pass the next inspection"),),
        inventory=(),
        feasibility=(),
        rules=(),
        privacy_mode=PrivacyMode.ANONYMOUS,
        roles={"shift_lead": "web_push"},
        acceptance=ACCEPTANCE,
    )
    assert SitePlan.model_validate_json(plan.model_dump_json()) == plan


def test_recommendation_round_trips_and_never_targets_a_person() -> None:
    rec = Recommendation(
        id="rec-1",
        status="proposed",
        scope_kind="station",
        scope_ids=("dock_3",),
        klass="test",
        provenance=Provenance.RECORDED,
        saw=(),
        why=(),
        do=(),
        who_role="dock_lead",
        revert="move replenishment back",
        expect=GAIN,
        safety=SafetyCheck(rules_checked=(), verdict="pass"),
        rank_score=1.0,
        numbers_total=14,
        numbers_reproduced=14,
    )
    assert Recommendation.model_validate_json(rec.model_dump_json()) == rec
    with pytest.raises(ValidationError):
        Recommendation.model_validate({**rec.model_dump(), "scope_kind": "person"})


def test_surprise_event_round_trips() -> None:
    window = SAMPLES[-1]
    assert isinstance(window, SurpriseWindow)
    event = SurpriseEvent(
        event_id="s1",
        camera_id="cam4",
        windows=(window,),
        heatmap_path="h.png",
        labels=("cardboard box",),
        peak_xy=(0.4, 0.6),
    )
    assert SurpriseEvent.model_validate_json(event.model_dump_json()) == event


def test_acceptance_all_passed_is_strict_and() -> None:
    assert ACCEPTANCE.all_passed
    failing = AcceptanceResult(
        checks=(*ACCEPTANCE.checks, AcceptanceCheck(item="x", check="nonvacuous", passed=False)),
        rounds=2,
    )
    assert not failing.all_passed


def test_acceptance_rounds_are_capped_at_two() -> None:
    with pytest.raises(ValidationError):
        AcceptanceResult(checks=(), rounds=3)


def test_predicate_trace_keeps_booleans_as_booleans() -> None:
    candidate = SAMPLES[14]
    assert isinstance(candidate, CandidateEvent)
    assert candidate.predicate_trace["entered"] is True
    assert candidate.predicate_trace["dwell_s"] == 8.0


def test_verifier_verdict_rejects_unknown_tier() -> None:
    with pytest.raises(ValidationError):
        VerifierVerdict.model_validate({**make_verdict().model_dump(), "tier": "gpt"})
