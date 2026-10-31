"""Plan activation (P3-08): SitePlan -> ActivePlan conversion, the gate, persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from rulecam.contracts import Calibration, PrivacyMode, Zone
from rulecam.evidence.chain import read_chain
from rulecam.runtime.activate import (
    NothingActivatableError,
    PlanActivator,
    RuntimeNotRunningError,
    active_plan_from,
    gate_rules,
    load_activation,
    pick_calibration,
    plan_zones,
    restore_activation,
    save_activation,
)
from tests.fakes import FakeClock, make_chunk
from tests.rules.helpers import CAL
from tests.runtime.activation_helpers import buffer_rule, checks_for, sink_rule, zoned_plan
from tests.runtime.fakes import make_loop

CAL2 = CAL.model_copy(update={"camera_id": "cam2"})


def test_polygon_and_buffer_zones_become_contract_zones() -> None:
    plan = zoned_plan(
        [sink_rule(), buffer_rule()], [*checks_for("in-sink"), *checks_for("near-fryer")]
    )
    activation = active_plan_from(plan)
    active = activation.active_plan
    assert [r.id for r in active.rules] == ["in-sink", "near-fryer"]
    assert active.privacy_mode is PrivacyMode.ANONYMOUS
    assert active.zones == {
        "sink_zone": Zone(
            zone_id="sink_zone",
            camera_id="cam1",
            kind="polygon",
            polygon_px=((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
        ),
        "fryer_buffer": Zone(
            zone_id="fryer_buffer",
            camera_id="cam2",
            kind="buffer",
            buffer_of_concept="fryer",
            buffer_m=1.5,
        ),
    }
    assert active.calibration is None
    assert len(active.plan_version_hash) == 64
    assert activation.rule_ids == ["in-sink", "near-fryer"]


def test_plan_version_hash_is_deterministic_and_tracks_content() -> None:
    plan = zoned_plan([sink_rule()], checks_for("in-sink"))
    first = active_plan_from(plan).active_plan.plan_version_hash
    assert active_plan_from(plan).active_plan.plan_version_hash == first
    bumped = plan.model_copy(update={"version": 2})
    assert active_plan_from(bumped).active_plan.plan_version_hash != first
    moved = zoned_plan([sink_rule(polygon=[[0, 0], [90, 0], [90, 90]])], checks_for("in-sink"))
    assert active_plan_from(moved).active_plan.plan_version_hash != first


def test_zone_names_declared_differently_are_left_to_each_rule() -> None:
    a = sink_rule("a")
    b = sink_rule("b", polygon=[[0, 0], [50, 0], [50, 50]])
    assert plan_zones([a, sink_rule("a2")]).keys() == {"sink_zone"}
    assert plan_zones([a, b]) == {}


def test_calibration_comes_from_the_run_for_the_most_used_camera() -> None:
    plan = zoned_plan(
        [sink_rule(), buffer_rule()], [*checks_for("in-sink"), *checks_for("near-fryer")]
    )
    active = active_plan_from(plan, calibrations={"cam2": CAL2}).active_plan
    assert active.calibration == CAL2
    rules = [sink_rule("x"), sink_rule("y"), buffer_rule()]
    assert pick_calibration(rules, {"cam1": CAL, "cam2": CAL2}) == CAL
    assert pick_calibration(rules, {"cam9": CAL}) is None
    tie: dict[str, Calibration] = {"cam1": CAL, "cam2": CAL2}
    assert pick_calibration([sink_rule(), buffer_rule()], tie) == CAL


def test_gate_pass_amber_and_fail() -> None:
    rules = [sink_rule("pass"), sink_rule("amber"), sink_rule("ci-red"), sink_rule("zone")]
    rules += [sink_rule("no-ci"), sink_rule("no-trace"), sink_rule("both")]
    checks = [
        *checks_for("pass"),
        *checks_for("amber", failing=["detect_lb>=0.8", "calibration_err<=tol"]),
        *checks_for("ci-red", failing=["ci_green"]),
        *checks_for("zone", failing=["zone_in_view"]),
        *checks_for("no-ci", omit=["ci_green", "nonvacuous"]),
        *checks_for("both", failing=["nonvacuous", "citation_text_match"]),
    ]
    gates = {g.rule_id: g for g in gate_rules(zoned_plan(rules, checks))}
    assert gates["pass"].status == "pass"
    assert gates["pass"].reasons == ()
    assert gates["amber"].status == "amber"
    assert gates["amber"].reasons == (
        "detect_lb>=0.8 failed (detect_lb>=0.8 detail)",
        "calibration_err<=tol failed (calibration_err<=tol detail)",
    )
    assert gates["ci-red"].status == "fail"
    assert gates["zone"].status == "fail"
    assert gates["no-ci"].reasons == ("ci_green not run", "nonvacuous not run")
    assert gates["no-trace"].reasons == ("no acceptance trace",)
    assert gates["both"].status == "fail"
    assert gates["both"].reasons == (
        "nonvacuous failed (nonvacuous detail)",
        "citation_text_match failed (citation_text_match detail)",
    )
    activation = active_plan_from(zoned_plan(rules, checks))
    assert activation.rule_ids == ["pass", "amber"]


def test_nothing_activatable_is_refused_with_reasons() -> None:
    plan = zoned_plan([sink_rule()], checks_for("in-sink", failing=["ci_green"]))
    with pytest.raises(NothingActivatableError) as err:
        active_plan_from(plan)
    assert "in-sink: ci_green failed" in str(err.value)
    assert err.value.gates[0].status == "fail"
    with pytest.raises(NothingActivatableError, match="has no rules"):
        active_plan_from(zoned_plan([], []))


def test_activator_persists_enforces_and_chains(tmp_path: Path) -> None:
    clock = FakeClock()
    loop, _, chain, _ = make_loop(tmp_path, clock)
    state = tmp_path / "runtime" / "active_plan.json"
    activator = PlanActivator(
        runtime=loop, chain=chain, state_path=state, calibrations=lambda _: {"cam1": CAL}
    )
    plan = zoned_plan([sink_rule()], checks_for("in-sink"))
    activation = activator.activate(plan)
    assert loop.plan == activation.active_plan
    assert loop.plan.calibration == CAL
    assert load_activation(state) == activation
    records = read_chain(chain.path_for(clock.now().date()))
    assert [r.kind for r in records] == ["plan", "plan"]
    approved = records[-1].payload
    assert approved["event"] == "plan_approved"
    assert approved["plan_id"] == "main-st"
    assert approved["version"] == 1
    assert approved["rules"] == ["in-sink"]
    assert approved["plan_version_hash"] == activation.active_plan.plan_version_hash
    assert len(loop.handle_chunk(make_chunk(index=0))) == 1


def test_activator_without_runtime_refuses_and_writes_nothing(tmp_path: Path) -> None:
    _, _, chain, _ = make_loop(tmp_path, FakeClock())
    state = tmp_path / "active_plan.json"
    activator = PlanActivator(runtime=None, chain=chain, state_path=state)
    with pytest.raises(RuntimeNotRunningError):
        activator.activate(zoned_plan([sink_rule()], checks_for("in-sink")))
    assert not state.exists()
    assert not chain.path_for(FakeClock().now().date()).exists()


def test_restore_after_restart(tmp_path: Path) -> None:
    clock = FakeClock()
    state = tmp_path / "active_plan.json"
    assert restore_activation(make_loop(tmp_path / "a", clock)[0], state) is None
    activation = active_plan_from(zoned_plan([sink_rule()], checks_for("in-sink")))
    save_activation(state, activation)
    restarted = make_loop(tmp_path / "b", clock)[0]
    assert restore_activation(restarted, state) == activation
    assert restarted.plan == activation.active_plan


def test_unreadable_state_is_ignored(tmp_path: Path) -> None:
    state = tmp_path / "active_plan.json"
    state.write_text("{not json", encoding="utf-8")
    assert load_activation(state) is None
