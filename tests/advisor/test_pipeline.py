"""Unit tests for Site Advisor reasoning pipeline (task P2-12).

Verifies all acceptance criteria:
1. Regex asserts there are no digits in model-written templates.
2. 'Add a forklift at the docks' is vetoed when 'ped-forklift-2m' is active.
3. Window guard handles unrecorded windows.
4. Explanation statuses follow data test results.
5. Claim linter downgrades 'caused' without a switchback.
6. Full pipeline emits 'advice.new' and constructs valid Recommendation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from rulecam.advisor.claims import (
    assert_template_has_no_digits,
    build_slot_claim,
    lint_claim_text,
    render_slot_claim,
)
from rulecam.advisor.pipeline import (
    AdvisorQuery,
    RecordedWindow,
    SiteAdvisorPipeline,
    WindowGuardResult,
)
from rulecam.advisor.rank import calculate_rank_score
from rulecam.advisor.safety import check_safety_veto
from rulecam.bus import Bus, Message, Topic
from rulecam.contracts.advisor import Change, Recommendation
from rulecam.contracts.core import Severity
from rulecam.contracts.rule_ir import (
    ActionSpec,
    ConceptSpec,
    DistanceM,
    RuleIR,
    RuleSource,
    ZoneSpec,
)
from tests.fakes import FakeLLM


def make_ped_forklift_rule() -> RuleIR:
    """Build the ped-forklift-2m active safety rule."""
    return RuleIR(
        id="ped-forklift-2m",
        source=RuleSource(
            kind="text", text="Maintain 2m clearance between pedestrians and forklifts"
        ),
        concepts={
            "person": ConceptSpec(prompt="person"),
            "forklift": ConceptSpec(prompt="forklift"),
        },
        zones={
            "dock_1": ZoneSpec(camera_id="cam1", kind="polygon"),
            "dock_3": ZoneSpec(camera_id="cam3", kind="polygon"),
            "aisle_1": ZoneSpec(camera_id="cam1", kind="polygon"),
        },
        predicate=DistanceM(
            op="distance_m",
            a="person.foot_point",
            b="forklift.foot_point",
            lt=2.0,
        ),
        severity=Severity.HIGH,
        actions=(ActionSpec(kind="log"),),
    )


def test_no_digits_in_templates() -> None:
    """Assert templates reject raw digits and accept {slots}."""
    # Valid: only {slots} and text, no raw digits
    assert_template_has_no_digits("Turnaround time reached {turnaround_min} minutes.")
    assert_template_has_no_digits(
        "Queue wait was {wait_s} seconds with {active_share} utilization."
    )

    # Invalid: contains literal digits
    with pytest.raises(ValueError, match="contains raw digit"):
        assert_template_has_no_digits("Turnaround time was 15 minutes.")

    with pytest.raises(ValueError, match="contains raw digit"):
        assert_template_has_no_digits("Dock 3 experienced delay.")

    with pytest.raises(ValueError, match="contains raw digit"):
        assert_template_has_no_digits("Increased by 20 percent.")


def test_claim_linter_downgrades_caused_without_switchback() -> None:
    """Claim linter must cap verbs: 'caused' is only allowed with switchback."""
    text = "The arrival bunching caused a 20 percent increase in dock delay."

    # Without switchback (Grade B before/after)
    linted_b = lint_claim_text(text, grade="B", is_switchback=False)
    assert "caused" not in linted_b
    assert "coincided with" in linted_b

    # With switchback and Grade A: 'caused' is permitted
    linted_a = lint_claim_text(text, grade="A", is_switchback=True)
    assert "caused" in linted_a

    # Banned phrases are stripped regardless
    banned_text = "Our hallucination-free advisor proves the change caused the gain."
    linted_banned = lint_claim_text(banned_text, grade="B", is_switchback=False)
    assert "hallucination-free" not in linted_banned
    assert "proves the change caused the gain" not in linted_banned


def test_slot_claim_build_and_render() -> None:
    """SlotClaim validates placeholders and renders verified numbers."""
    claim = build_slot_claim(
        template="Dock turnaround was {turnaround_min} minutes with {wait_min} minutes queue wait.",
        slots={"turnaround_min": "metric_turnaround", "wait_min": "metric_wait"},
        grade="B",
        is_switchback=False,
    )
    rendered = render_slot_claim(claim, {"metric_turnaround": "45.2", "metric_wait": "18.5"})
    assert rendered == "Dock turnaround was 45.2 minutes with 18.5 minutes queue wait."


def test_add_forklift_at_docks_is_vetoed_when_ped_forklift_2m_active() -> None:
    """'Add a forklift at the docks' is vetoed when ped-forklift-2m is active."""
    rule = make_ped_forklift_rule()

    # Change proposing to add a forklift at dock_3 without mitigation
    change_unmitigated = Change(
        op="add_capacity",
        params={
            "resource": "forklift",
            "zone": "dock_3",
            "description": "Add a forklift at the docks",
        },
    )
    safety_veto = check_safety_veto((change_unmitigated,), [rule])
    assert safety_veto.verdict == "veto"
    assert len(safety_veto.considered_not_recommended) == 1
    _rejected_desc, reason = safety_veto.considered_not_recommended[0]
    assert "ped-forklift-2m" in reason
    assert "increases collision risk" in reason

    # Change with explicit safety mitigation passes with mitigation
    change_mitigated = Change(
        op="add_capacity",
        params={
            "resource": "forklift",
            "zone": "dock_3",
            "mitigation": "Physical pedestrian barrier fence installed along apron",
        },
    )
    safety_mitigated = check_safety_veto((change_mitigated,), [rule])
    assert safety_mitigated.verdict == "pass_with_mitigation"

    # Non-vehicle change (e.g. reschedule arrivals) passes cleanly
    change_reschedule = Change(
        op="reschedule_arrivals",
        params={"station": "dock_3", "window": "stagger_30min"},
    )
    safety_pass = check_safety_veto((change_reschedule,), [rule])
    assert safety_pass.verdict == "pass"


def test_ranking_formula_and_classes() -> None:
    """Test rank_score calculation and classification into do, test, hold."""
    # Vetoed change always holds with score 0
    rule = make_ped_forklift_rule()
    vetoed_safety = check_safety_veto(
        (Change(op="add_capacity", params={"resource": "forklift", "zone": "dock_3"}),),
        [rule],
    )
    score, klass = calculate_rank_score(
        p20_weekly_value=100.0, op="add_capacity", safety=vetoed_safety
    )
    assert score == 0.0
    assert klass == "hold"

    # High return, easily reversible operation -> 'do'
    score_do, klass_do = calculate_rank_score(p20_weekly_value=80.0, op="reschedule_arrivals")
    assert score_do >= 30.0
    assert klass_do == "do"

    # Moderate return -> 'test'
    score_test, klass_test = calculate_rank_score(p20_weekly_value=30.0, op="queue_topology")
    assert 10.0 <= score_test < 30.0
    assert klass_test == "test"


@pytest.mark.asyncio
async def test_window_guard_handles_unrecorded_windows() -> None:
    """Window guard must flag unrecorded windows with available sessions."""
    t0 = datetime(2026, 10, 31, 8, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 31, 16, 0, tzinfo=UTC)
    recorded = RecordedWindow(start=t0, end=t1, station_id="dock_3")

    pipeline = SiteAdvisorPipeline(spark=FakeLLM(), bus=Bus(), recorded_windows=[recorded])

    # Query for unrecorded time window (next day)
    unrecorded_start = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    unrecorded_end = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    query = AdvisorQuery(
        question="Why was dock turnaround slow?",
        station_id="dock_3",
        start_window=unrecorded_start,
        end_window=unrecorded_end,
    )

    result = await pipeline.run(query=query, active_rules=(), metric_values={})
    assert isinstance(result, WindowGuardResult)
    assert result.recorded is False
    assert "was not recorded" in result.message
    assert len(result.available_windows) == 1
    assert result.available_windows[0].station_id == "dock_3"


@pytest.mark.asyncio
async def test_explanation_statuses_follow_data_test_results() -> None:
    """Explanation statuses must strictly reflect measured data thresholds."""
    pipeline = SiteAdvisorPipeline(spark=FakeLLM(), bus=Bus())

    # Given metrics where active_share >= 0.7 (supported) and wait < 21 (ruled_out)
    metrics = {
        "forklift_active_share": 0.85,  # threshold 0.7 -> supported
        "wait_for_forklift": 10.0,  # threshold 30.0, 10 < 21 -> ruled_out
        "handling": 35.0,  # threshold 45.0, 35 >= 31.5 -> partly
        # aisle_blocked_min is omitted -> untestable
    }

    explanations = pipeline.explain_causes("dock", metrics)
    expl_map = {e.id: e for e in explanations}

    assert expl_map["replenishment_wave"].status == "supported"
    assert expl_map["replenishment_wave"].share_of_gap == 0.42

    assert expl_map["off_appointment_arrivals"].status == "ruled_out"
    assert expl_map["off_appointment_arrivals"].share_of_gap == 0.0

    assert expl_map["forklift_travel_distance"].status == "partly"
    assert expl_map["forklift_travel_distance"].share_of_gap is not None

    assert expl_map["staging_overflow"].status == "untestable"
    assert expl_map["staging_overflow"].share_of_gap is None


@pytest.mark.asyncio
async def test_full_pipeline_emits_advice_new_and_returns_recommendation() -> None:
    """Full pipeline generates Recommendation and publishes Topic.ADVICE_NEW."""
    bus = Bus()
    pipeline = SiteAdvisorPipeline(spark=FakeLLM(), bus=bus)
    rule = make_ped_forklift_rule()

    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus._queues[Topic.ADVICE_NEW].append(queue)

    query = AdvisorQuery(
        question="Why is dock 3 queue backed up?",
        station_id="dock_3",
    )

    metric_values = {
        "forklift_active_share": 0.88,
        "wait_for_forklift": 38.0,
        "handling": 52.0,
        "aisle_blocked_min": 8.0,
    }

    result = await pipeline.run(
        query=query,
        active_rules=[rule],
        metric_values=metric_values,
        k_tracks=15,
        num_shifts=5,
    )

    assert isinstance(result, Recommendation)
    assert result.scope_kind == "station"
    assert result.scope_ids == ("dock_3",)
    assert len(result.saw) == 1
    assert len(result.why) == 4
    assert len(result.do) == 1
    assert result.expect.metric == "turnaround_minutes"

    # Verify event published on bus
    assert not queue.empty()
    msg = queue.get_nowait()
    assert msg.topic == Topic.ADVICE_NEW
    payload = msg.payload
    assert payload["recommendation_id"] == result.id
    assert payload["station_id"] == "dock_3"
    assert payload["klass"] == result.klass
