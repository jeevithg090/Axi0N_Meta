"""Tests for Site Advisor kitchen pass-window station type (task P2-S3, SA §6)."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from rulecam.advisor.claims import assert_no_digits
from rulecam.advisor.kitchen import (
    KITCHEN_HYPOTHESES,
    check_kitchen_hygiene_guard,
    littles_law_ticket_time,
)
from rulecam.advisor.pipeline import (
    RecordedWindow,
    station_type,
)
from rulecam.advisor.safety import assess_change
from rulecam.bus import Topic
from rulecam.contracts.advisor import Change, Recommendation
from rulecam.contracts.rule_ir import (
    ActionSpec,
    ConceptSpec,
    DistanceM,
    RuleIR,
    RuleSource,
    Severity,
    ZoneSpec,
)
from tests.advisor.advisor_fakes import (
    T0,
    T1,
    FakeDevil,
    FakeStats,
    RecordingBus,
    Result,
    advice,
    make_pipeline,
)
from tests.fakes import FakeLLM

DIGIT = re.compile(r"\d")


def pass_window_proximity_rule(*, rule_id: str = "pass-window-crowding-1m") -> RuleIR:
    """A proximity rule covering the pass window zone."""
    distance = DistanceM(a="person.foot_point", b="person.foot_point", lt=1.0)
    return RuleIR(
        id=rule_id,
        source=RuleSource(kind="text", text="Keep 1 m clearance at pass window"),
        concepts={"person": ConceptSpec(prompt="person")},
        zones={"pass_window": ZoneSpec(camera_id="cam_pass", kind="polygon")},
        predicate=distance,
        severity=Severity.HIGH,
        actions=(ActionSpec(kind="log"),),
    )


def kitchen_results() -> dict[str, Result]:
    """Synthetic metric results for the kitchen station hypotheses."""
    return {
        "pass_wait": Result("mr_pass", "pass_wait", 3.6, 3.0, 4.2, "min", station_id="pass_window"),
        "ticket_time": Result(
            "mr_ticket", "ticket_time", 18.0, 16.0, 20.0, "min", station_id="pass_window"
        ),
        "station_crowding": Result(
            "mr_crowd", "station_crowding", 0.75, 0.71, 0.79, "share", station_id="pass_window"
        ),
        "handwash_compliance": Result(
            "mr_wash", "handwash_compliance", 0.62, 0.55, 0.69, "share", station_id="pass_window"
        ),
        "handsink_blocked_min": Result(
            "mr_sink", "handsink_blocked_min", 4.5, 3.5, 5.5, "min", station_id="pass_window"
        ),
        "dish_pit_backlog": Result(
            "mr_dish", "dish_pit_backlog", 2.0, 1.0, 3.0, "count", station_id="pass_window"
        ),
        "cleaning_cadence_hours": Result(
            "mr_clean", "cleaning_cadence_hours", 4.8, 4.2, 5.4, "h", station_id="pass_window"
        ),
    }


def ask_kitchen(**overrides: object) -> dict[str, object]:
    """Return a valid Ask answer for the pass window."""
    answer: dict[str, object] = {
        "metric_ids": ["pass_wait"],
        "scope_kind": "station",
        "scope_ids": ["pass_window"],
        "start": T0.isoformat(),
        "end": T1.isoformat(),
    }
    answer.update(overrides)
    return answer


# --- hypothesis statement checks -------------------------------------------------------------


def test_kitchen_catalogue_statements_have_no_digits() -> None:
    for hypothesis in KITCHEN_HYPOTHESES:
        assert not DIGIT.search(hypothesis.statement), hypothesis.id
        assert_no_digits(hypothesis.statement)


def test_kitchen_catalogue_units_and_keywords() -> None:
    for hyp in KITCHEN_HYPOTHESES:
        assert hyp.unit in ("min", "share", "count", "h")
        assert hyp.threshold > 0.0
        assert len(hyp.keywords) >= 2
        for kw in hyp.keywords:
            assert kw == kw.lower()


# --- Little's law ----------------------------------------------------------------------------


def test_littles_law_ticket_time_calculation() -> None:
    # 10 orders in flight, 2 orders/min throughput -> 5 minutes average ticket time
    assert littles_law_ticket_time(10.0, 2.0) == 5.0
    # 15 orders in flight, 1 order/min -> 15 min
    assert littles_law_ticket_time(15.0, 1.0) == 15.0


def test_littles_law_rejects_non_positive_throughput_or_negative_wip() -> None:
    with pytest.raises(ValueError, match="strictly positive"):
        littles_law_ticket_time(10.0, 0.0)
    with pytest.raises(ValueError, match="strictly positive"):
        littles_law_ticket_time(10.0, -1.0)
    with pytest.raises(ValueError, match="cannot be negative"):
        littles_law_ticket_time(-5.0, 2.0)


# --- Hygiene guardrail -----------------------------------------------------------------------


def test_check_kitchen_hygiene_guard_passes_and_fails_accurately() -> None:
    passed = check_kitchen_hygiene_guard(0.92, threshold=0.85)
    assert passed.compliant
    assert "satisfies" in passed.reason

    failed = check_kitchen_hygiene_guard(0.68, threshold=0.85)
    assert not failed.compliant
    assert "fell below" in failed.reason
    assert "68.0%" in failed.reason


# --- Station type resolution -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("station_id", "expected"),
    [
        ("pass_window", "kitchen"),
        ("pass_1", "kitchen"),
        ("kitchen_pass", "kitchen"),
        ("kitchen_main", "kitchen"),
        ("dock_3", "dock"),
        ("aisle_1", "aisle"),
    ],
)
def test_station_type_mapping(station_id: str, expected: str) -> None:
    assert station_type(station_id) == expected


# --- Explain and Devil's advocate for kitchen ------------------------------------------------


def test_kitchen_explanation_statuses_follow_tests() -> None:
    results = kitchen_results()
    results["dish_pit_backlog"] = Result(
        "mr_dish", "dish_pit_backlog", 2.0, 1.0, 3.0, "count", station_id="pass_window"
    )
    # under k -> untestable
    results["ticket_time"] = replace(results["ticket_time"], n_tracks=3)

    primary = results["pass_wait"]
    stats = FakeStats(
        flagged=(primary,),
        results=results,
        shares={"pass_wait": 0.44, "station_crowding": 0.3},
    )
    pipeline = make_pipeline(FakeLLM(), stats=stats)
    explanations, used = pipeline.explain(primary, T0, T1)
    by_id = {e.id: e for e in explanations}

    # pass_unattended threshold 2.0 min, interval [3.0, 4.2] -> supported
    assert by_id["pass_unattended"].status == "supported"
    # station_crowding threshold 0.7, interval [0.71, 0.79] -> supported
    assert by_id["station_crowding"].status == "supported"
    assert by_id["station_crowding"].share_of_gap == 0.3
    # dish_pit_backlog threshold 5.0, interval [1.0, 3.0] -> ruled_out (hi < 5.0)
    assert by_id["dish_pit_backlog"].status == "ruled_out"
    # ticket_time has n_tracks=3 -> untestable
    assert by_id["ticket_time_high"].status == "untestable"

    assert "mr_pass" in {r.id for r in used}
    assert "mr_dish" in {r.id for r in used}


def test_kitchen_devil_advocate_matching() -> None:
    devil = FakeDevil(
        proposals=[
            "It is just random noise.",
            "Bus tubs backlogged at the dish pit.",
            "An electrical storm knocked out lighting.",
            "Plates waited unattended because staff were busy.",
        ]
    )
    results = kitchen_results()
    primary = results["pass_wait"]
    stats = FakeStats(flagged=(primary,), results=results)
    pipeline = make_pipeline(FakeLLM(), stats=stats, devil=devil)
    explanations, _ = pipeline.explain(primary, T0, T1)
    challenged = pipeline.challenge(primary, explanations)

    assert [(e.id, e.status) for e in challenged] == [
        ("devil_0", "ruled_out"),
        ("devil_1", "ruled_out"),  # matches dish pit backlog (ruled out)
        ("devil_2", "untestable"),  # no kitchen keywords match
        ("devil_3", "supported"),  # matches pass unattended (supported)
    ]


# --- Safety veto for kitchen -----------------------------------------------------------------


def test_kitchen_non_agent_pass_bell_passes_without_mitigation() -> None:
    change = Change(op="add_capacity", params={"resource": "pass_bell", "station": "pass_window"})
    outcome = assess_change(
        change,
        [pass_window_proximity_rule()],
        scope_ids=("pass_window",),
        mitigations={},
    )
    assert outcome.verdict == "pass"


def test_kitchen_extra_runner_vetoed_under_proximity_rule_without_mitigation() -> None:
    change = Change(op="add_capacity", params={"resource": "runner", "station": "pass_window"})
    outcome = assess_change(
        change,
        [pass_window_proximity_rule()],
        scope_ids=("pass_window",),
        mitigations={},
    )
    assert outcome.verdict == "veto"
    assert "pass-window-crowding-1m" in outcome.reasons[0]


def test_kitchen_extra_runner_passes_with_owner_mitigation() -> None:
    change = Change(op="add_capacity", params={"resource": "runner", "station": "pass_window"})
    outcome = assess_change(
        change,
        [pass_window_proximity_rule()],
        scope_ids=("pass_window",),
        mitigations={"pass-window-crowding-1m": "designated one-way runner lane marked on floor"},
    )
    assert outcome.verdict == "pass_with_mitigation"


# --- Full end-to-end pipeline run for kitchen pass window ------------------------------------


HOST_RUNS_PASS: dict[str, object] = {
    "tier": "elevate",
    "op": "resequence",
    "params": {"action": "host or busser runs when waitlist is empty", "equipment": "pass bell"},
    "who_role": "host",
    "revert": "stop host running pass",
}

HIRE_RUNNER: dict[str, object] = {
    "tier": "exploit",
    "op": "add_capacity",
    "params": {"resource": "runner", "station": "pass_window"},
    "who_role": "general_manager",
    "revert": "terminate trial runner shift",
}


def test_full_run_kitchen_recommends_safe_pass_resequence_and_publishes() -> None:
    bus = RecordingBus()
    results = kitchen_results()
    primary = results["pass_wait"]
    stats = FakeStats(
        flagged=(primary,),
        results=results,
        shares={"pass_wait": 0.44},
    )
    # Runner is exploit (evaluated first, vetoed);
    # host running pass is elevate (evaluated second, succeeds)
    spark = FakeLLM(
        [
            ask_kitchen(),
            advice(HOST_RUNS_PASS, HIRE_RUNNER),
        ]
    )
    pipeline = make_pipeline(
        spark,
        stats=stats,
        bus=bus,
        rules=[pass_window_proximity_rule()],
        windows=(RecordedWindow(T0, T1, "pass_window"),),
    )

    result = pipeline.run("Why were plates waiting on the pass window Friday night?")

    assert isinstance(result, Recommendation)
    assert result.do == (
        Change.model_validate({"op": "resequence", "params": HOST_RUNS_PASS["params"]}),
    )
    assert result.who_role == "host"
    assert result.klass == "do"
    assert result.numbers_total > 0
    # Published on advice.new topic
    assert bus.published == [(Topic.ADVICE_NEW, result)]
    # Safety record documents the considered but vetoed runner
    assert any("runner" in change for change, _ in result.safety.considered_not_recommended)
