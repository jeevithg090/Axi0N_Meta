"""Rule CI part 1: boundary traces and mutation tests (task P2-08)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from rulecam.bus import Message, Topic
from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.plan import AcceptanceCheck, AcceptanceResult, SitePlan
from rulecam.contracts.rule_ir import RuleIR
from rulecam.rule_ci import (
    CIResult,
    RuleCIReport,
    generate_mutations,
    generate_rule_traces,
    prove_plan_hook,
    run_rule_ci,
)
from rulecam.rule_ci.mutations import widen_zone
from rulecam.rule_ci.runner import fires

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def _raw(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((FIXTURES / f"{name}.rule.yaml").read_text("utf-8"))
    return data


def _rule(name: str) -> RuleIR:
    return RuleIR.model_validate(_raw(name))


class Recorder:
    """Collects what Rule CI publishes."""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.messages.append(Message(topic=topic, payload=payload))


# --- required tests -----------------------------------------------------------------------------


def test_handwash_passes_its_three_boundary_traces() -> None:
    """MP §7.3 `tests.boundary`: 19.5 s fails, 20.5 s passes, two 12 s washes 2 s apart pass."""
    rule = _rule("wash-before-line")
    traces = {t.name: t for t in generate_rule_traces(rule)}

    mp_block = {"total_s_gte_19.5s": True, "total_s_gte_20.5s": False, "max_gap_s_2s": False}
    for name, violation in mp_block.items():
        assert traces[name].expected_violation is violation
        assert fires(rule, traces[name]) is violation, name
    assert "two 12 s washes 2 s apart" in traces["max_gap_s_2s"].description

    report = run_rule_ci(rule)
    assert report.traces_passed == report.traces_total == len(traces)
    assert report.failures == ()


def test_all_standard_mutants_of_the_handwash_rule_are_killed() -> None:
    rule = _rule("wash-before-line")
    mutants = generate_mutations(rule)
    assert [m.name for m in mutants] == [
        "scale_threshold_0.1",
        "scale_threshold_10",
        "switch_semantics",
        "widen_zone:line_entry",
        "widen_zone:sink_zone",
    ]

    report = run_rule_ci(rule)
    assert (
        report.summary() == f"boundary {report.traces_total}/{report.traces_total}, mutations 5/5"
    )
    assert report.ci_green


def test_all_standard_mutants_of_the_distance_rule_are_killed() -> None:
    rule = _rule("forklift-proximity")
    assert [m.name for m in generate_mutations(rule)] == [
        "scale_threshold_0.1",
        "scale_threshold_10",
        "flip_lt_gt",
        "swap_all_any",
    ]
    report = run_rule_ci(rule)
    assert (report.mutants_killed, report.mutants_total) == (4, 4)
    assert report.traces_passed == report.traces_total
    assert report.ci_green


@pytest.mark.parametrize(
    ("name", "path", "value"),
    [
        ("wash-before-line", ("requirement", "total_s_gte"), 0.0),  # any touch counts as a wash
        ("wash-before-line", ("require_within_s",), 0.0),  # no wash is ever recent enough
        ("forklift-proximity", ("of", 0, "lt"), 0.0),  # never closer than 0 m
    ],
    ids=["dwell-0s", "window-0s", "distance-0m"],
)
def test_a_deliberately_vacuous_rule_is_flagged(
    name: str, path: tuple[str | int, ...], value: float
) -> None:
    raw = _raw(name)
    node = raw["predicate"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value

    report = run_rule_ci(RuleIR.model_validate(raw))

    assert report.nonvacuous is False
    assert not report.ci_green


def test_the_report_schema_validates_and_gates_ci_green() -> None:
    report = RuleCIReport(
        rule_id="r",
        traces_passed=5,
        traces_total=5,
        mutants_killed=9,
        mutants_total=10,
        nonvacuous=True,
    )
    assert RuleCIReport.model_validate_json(report.model_dump_json()) == report
    assert report.ci_green
    assert report.mutation_score == 0.9
    assert not report.model_copy(update={"traces_passed": 4}).ci_green
    low = report.model_copy(update={"mutants_killed": 8})
    assert not low.ci_green
    assert low.model_copy(
        update={"justification": "equivalent mutant: zone at frame edge"}
    ).ci_green
    assert not report.model_copy(update={"traces_passed": 0, "traces_total": 0}).ci_green
    payload = CIResult.of(report).model_dump(mode="json")
    assert payload["rule_id"] == "r"
    assert payload["ci_green"] is True
    with pytest.raises(ValueError, match="extra"):
        RuleCIReport.model_validate({**report.model_dump(), "unknown": 1})


# --- traces -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["wash-before-line", "forklift-proximity", "excavator-swing-radius", "fire-exit-blocked"],
)
def test_every_supported_example_rule_is_green(name: str) -> None:
    report = run_rule_ci(_rule(name))
    assert report.ci_green, report.failures
    assert report.nonvacuous
    assert report.mutants_killed == report.mutants_total


def test_boundary_pairs_straddle_the_rules_own_thresholds() -> None:
    raw = _raw("wash-before-line")
    raw["predicate"]["requirement"]["total_s_gte"] = 30
    traces = generate_rule_traces(RuleIR.model_validate(raw))
    pair = sorted((t.value, t.expected_violation) for t in traces if t.pair == "total_s_gte")
    assert pair == [(29.2, True), (30.8, False)]  # 30 s -+ 2.5%, on the 10 Hz grid

    forklift = {t.name: t.value for t in generate_rule_traces(_rule("forklift-proximity"))}
    assert forklift["distance_lt_1.95m"] == pytest.approx(1.95)
    assert forklift["distance_lt_2.05m"] == pytest.approx(2.05)
    assert forklift["speed_mps_gt_0.21mps"] == pytest.approx(0.21)
    excavator = {
        t.name: t.expected_violation for t in generate_rule_traces(_rule("excavator-swing-radius"))
    }
    assert (excavator["persist_s_0.9s"], excavator["persist_s_1.1s"]) == (False, True)


def test_trace_families_cover_the_task_book_list() -> None:
    kinds = {t.kind for t in generate_rule_traces(_rule("wash-before-line"))}
    assert kinds == {"boundary", "gap", "window", "interruption", "id_swap", "order", "zone_edge"}
    edges = {
        t.name for t in generate_rule_traces(_rule("fire-exit-blocked")) if t.kind == "zone_edge"
    }
    assert edges == {"zone_inside", "zone_outside"}


def test_future_semantics_gets_its_own_traces_and_a_decision_horizon() -> None:
    raw = _raw("wash-before-line")
    raw["predicate"]["semantics"] = "future"
    rule = RuleIR.model_validate(raw)
    traces = {t.name: t for t in generate_rule_traces(rule)}
    assert traces["washes_before_entering"].expected_violation
    assert (traces["total_s_gte_19.5s"].t1 - traces["total_s_gte_19.5s"].t0).total_seconds() > 300
    assert run_rule_ci(rule).ci_green


def test_traces_are_deterministic() -> None:
    rule = _rule("wash-before-line")
    assert generate_rule_traces(rule) == generate_rule_traces(rule)


def test_an_unsupported_rule_fails_honestly() -> None:
    report = run_rule_ci(_rule("person-down"))
    assert (report.traces_total, report.mutants_killed) == (0, 0)
    assert report.mutants_total == 3
    assert not report.ci_green
    assert not report.nonvacuous
    assert report.failures[0] == "no boundary traces: no boundary-trace generator for persist(pose)"


# --- mutations ----------------------------------------------------------------------------------


def test_surviving_mutants_are_reported_and_scored() -> None:
    rule = _rule("wash-before-line")
    only = [t for t in generate_rule_traces(rule) if t.name == "total_s_gte_20.5s"]
    report = run_rule_ci(rule, traces=only)
    assert (report.mutants_killed, report.mutants_total) == (1, 5)  # only x10 flips 20.5 s
    survivors = [f for f in report.failures if f.startswith("mutant ")]
    assert len(survivors) == 4
    assert report.mutation_score == pytest.approx(0.2)
    assert not report.ci_green
    assert run_rule_ci(rule, traces=only, justification="demo").ci_green


def test_operators_apply_where_the_rule_has_something_to_change() -> None:
    raw = _raw("excavator-swing-radius")
    raw["predicate"] = {"op": "not", "of": raw["predicate"]}
    names = {m.name for m in generate_mutations(RuleIR.model_validate(raw))}
    assert names == {
        "scale_threshold_0.1",
        "scale_threshold_10",
        "flip_lt_gt",
        "drop_not",
        "swap_all_any",
        "remove_persist",
    }
    scaled = next(
        m
        for m in generate_mutations(_rule("excavator-swing-radius"))
        if m.name == "scale_threshold_10"
    )
    assert scaled.rule.model_dump()["predicate"]["seconds"] == 10.0
    assert scaled.rule.model_dump()["predicate"]["of"]["of"][0]["lt"] == 20.0


def test_widening_a_polygon_keeps_it_centred() -> None:
    spec = _rule("wash-before-line").zones["line_entry"]  # x 0-400, y 0-300
    wider = widen_zone(spec)
    assert wider is not None
    assert wider.polygon_px is not None
    xs, ys = [x for x, _ in wider.polygon_px], [y for _, y in wider.polygon_px]
    assert (min(xs), max(xs), min(ys), max(ys)) == (-100, 500, -75, 375)
    buffer = widen_zone(_rule("wash-before-line").zones["sink_zone"])
    assert buffer is not None
    assert buffer.buffer_m == pytest.approx(0.9)


# --- bus and the prove hook ---------------------------------------------------------------------


def _plan(*rules: RuleIR, checks: tuple[AcceptanceCheck, ...] = ()) -> SitePlan:
    return SitePlan(
        plan_id="p",
        site_name="Test",
        setting_inferred="kitchen",
        languages=("en",),
        request=(),
        inventory=(),
        feasibility=(),
        rules=rules,
        privacy_mode=PrivacyMode.STANDARD,
        acceptance=AcceptanceResult(checks=checks, rounds=1),
    )


def test_the_report_is_published_on_ci_result() -> None:
    bus = Recorder()
    report = run_rule_ci(_rule("forklift-proximity"), bus=bus)  # type: ignore[arg-type]
    assert [m.topic for m in bus.messages] == [Topic.CI_RESULT]
    payload = bus.messages[0].payload
    assert payload == CIResult.of(report)


def test_the_prove_hook_records_ci_green_and_nonvacuous_per_rule() -> None:
    bus = Recorder()
    zone = AcceptanceCheck(item="wash-before-line", check="zone_in_view", passed=True)
    stale = AcceptanceCheck(item="wash-before-line", check="ci_green", passed=False)
    plan = _plan(_rule("wash-before-line"), _rule("person-down"), checks=(zone, stale))

    proved = prove_plan_hook(bus)(plan)  # type: ignore[arg-type]

    checks = [(c.item, c.check, c.passed) for c in proved.acceptance.checks]
    assert checks == [
        ("wash-before-line", "zone_in_view", True),
        ("wash-before-line", "ci_green", True),
        ("wash-before-line", "nonvacuous", True),
        ("person-down", "ci_green", False),
        ("person-down", "nonvacuous", False),
    ]
    assert proved.acceptance.rounds == 1
    assert proved.acceptance.checks[1].detail.startswith("boundary 13/13, mutations 5/5")
    assert "persist(pose)" in proved.acceptance.checks[3].detail
    assert [m.payload.rule_id for m in bus.messages] == ["wash-before-line", "person-down"]  # type: ignore[attr-defined]
    assert prove_plan_hook()(proved) == proved  # re-running replaces, never duplicates


def test_an_engine_error_counts_as_a_trace_failure() -> None:
    rule = _rule("wash-before-line")
    trace = generate_rule_traces(rule)[0]
    broken = rule.model_copy(update={"zones": {}})  # the engine cannot resolve the zones
    outcome = fires(broken, trace)
    assert isinstance(outcome, str)
    assert outcome.startswith("error:")
