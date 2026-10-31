"""Rule CI boundary traces for buffer-zone rules: `inside(x, buffer of y)` [+ `moving`] (P2-08)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from shapely.geometry import LineString, Point

from rulecam.contracts.core import Calibration, PrivacyMode
from rulecam.contracts.plan import AcceptanceResult, SitePlan
from rulecam.contracts.rule_ir import Moving, RuleIR
from rulecam.perception.calibration import px_to_m
from rulecam.rule_ci import UnsupportedRuleError, generate_mutations, prove_plan_hook, run_rule_ci
from rulecam.rule_ci.buffer_traces import (
    BufferScene,
    BufferShape,
    GroundPlane,
    Stay,
    calibrated_cameras,
)
from rulecam.rule_ci.generate import generate_rule_traces
from rulecam.rule_ci.geometry import synthetic_calibration
from rulecam.rule_ci.models import TraceCase
from rulecam.rule_ci.runner import fires

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rules"
LIVE = "forklift-buffer"
INSIDE = {"op": "inside", "object": "person", "zone": "forklift_buffer"}
MOVING = {"op": "moving", "object": "forklift", "speed_mps_gt": 0.2}
PERSPECTIVE = Calibration(
    camera_id="cam1",
    homography=((0.004, 0.001, -1.0), (0.0002, 0.012, -2.0), (0.0, 0.0009, 1.0)),
    method="clicks4",
    err_m=0.05,
)


def _raw(name: str = LIVE) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((FIXTURES / f"{name}.rule.yaml").read_text("utf-8"))
    return data


def _rule(**update: Any) -> RuleIR:
    return RuleIR.model_validate({**_raw(), **update})


def _ground_distance(trace: TraceCase, index: int) -> float:
    """Subject-to-forklift-footprint distance at a sample, re-derived from the tracks alone."""
    by_concept = {t.concept: t for t in trace.tracks if t.concept != "person"}
    forklift = by_concept["forklift"].points[index]
    person = next(
        p for t in trace.tracks if t.concept == "person" for p in t.points if p.frame_idx == index
    )
    x1, _, x2, y2 = forklift.box_xyxy
    footprint = px_to_m(trace.calibration, [(x1, y2), (x2, y2)])
    (foot,) = px_to_m(trace.calibration, [person.foot_px])
    assert person.foot_m is not None
    assert np.allclose(foot, person.foot_m)
    return float(LineString(footprint).distance(Point(person.foot_m)))


# --- the live finding ---------------------------------------------------------------------------


def test_the_live_buffer_rule_passes_rule_ci_with_its_calibration() -> None:
    rule = _rule()
    report = run_rule_ci(rule)

    assert report.ci_green, report.failures
    assert report.nonvacuous
    assert report.failures == ()
    assert report.summary() == "boundary 15/15, mutations 5/5"  # + kinematic and operator
    assert [m.name for m in generate_mutations(rule)] == [
        "scale_threshold_0.1",
        "scale_threshold_10",
        "swap_all_any",
        "widen_zone:forklift_buffer",
        "drop_operator_exclusion",
    ]
    traces = {t.name: t for t in generate_rule_traces(rule)}
    assert {name: t.expected_violation for name, t in traces.items()} == {
        "zone_inside": True,
        "zone_outside": False,
        "speed_mps_gt_0.21mps": True,
        "speed_mps_gt_0.19mps": False,
        "stationary": False,
        "interrupted_stay": True,
        "id_swap_mid_stay": True,
        # kinematic_traces (WS1d): approach and pass-by pairs, the live co-moving 6.4 m case
        "approach_lt_1.95m": True,
        "approach_lt_2.05m": False,
        "pass_lt_1.95m": True,
        "pass_lt_2.05m": False,
        "co_moving_far_6.4m": False,
        # operator vs pedestrian (WS1c)
        "operator_riding_0.5m": False,
        "pedestrian_beside_0.5m": True,
        "pedestrian_in_front_0.5m": True,
    }
    assert {t.kind for t in traces.values()} >= {"zone_edge", "boundary", "interruption", "id_swap"}
    assert len({t.tracks[0].track_id for t in traces.values()}) == 1
    assert len(traces["id_swap_mid_stay"].tracks) == 3  # two person identities + the forklift


def test_zone_edges_sit_5_cm_either_side_of_buffer_m_by_construction() -> None:
    traces = {t.name: t for t in generate_rule_traces(_rule())}
    near = 30  # inside the stay: after the 2 s lead-in at 10 Hz
    assert _ground_distance(traces["zone_inside"], near) == pytest.approx(1.95)
    assert _ground_distance(traces["zone_outside"], near) == pytest.approx(2.05)
    assert _ground_distance(traces["zone_inside"], 0) == pytest.approx(5.0)  # lead-in: 2b + 1


def test_every_mutant_is_killed_by_a_trace_that_flips() -> None:
    rule = _rule()
    traces = {t.name: t for t in generate_rule_traces(rule)}
    flips = {
        m.name: sorted(n for n, t in traces.items() if fires(m.rule, t) != fires(rule, t))
        for m in generate_mutations(rule)
    }
    assert "zone_outside" in flips["widen_zone:forklift_buffer"]  # 2.05 m inside a 3 m buffer
    assert "speed_mps_gt_0.19mps" in flips["scale_threshold_0.1"]
    assert "speed_mps_gt_0.21mps" in flips["scale_threshold_10"]
    assert {"zone_outside", "stationary"} <= set(flips["swap_all_any"])


def test_without_calibration_it_fails_with_the_reason() -> None:
    rule = _rule(calibration=None)
    report = run_rule_ci(rule)

    assert not report.ci_green
    assert not report.nonvacuous
    assert (report.traces_total, report.mutants_killed, report.mutants_total) == (0, 0, 5)
    assert report.failures[0].startswith("no boundary traces: buffer zone needs calibration")
    assert "camera 'cam1'" in report.failures[0]
    assert "no boundary-trace generator" not in " ".join(report.failures)
    other_camera = _rule(calibration="ground-plane@cam2")
    with pytest.raises(UnsupportedRuleError, match="buffer zone needs calibration"):
        generate_rule_traces(other_camera)


# --- shapes -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "predicate",
    [
        {"op": "all", "of": [MOVING, INSIDE]},
        {"op": "persist", "seconds": 1.0, "of": {"op": "all", "of": [INSIDE, MOVING]}},
        {"op": "all", "of": [INSIDE, {**MOVING, "object": "person", "speed_mps_gt": 0.5}]},
        {"op": "persist", "seconds": 3.0, "of": INSIDE},
        INSIDE,
    ],
    ids=["moving-first", "persist", "moving-subject", "inside-persist", "inside-alone"],
)
def test_every_buffer_shape_is_green_with_all_mutants_killed(predicate: dict[str, Any]) -> None:
    report = run_rule_ci(_rule(predicate=predicate))
    assert report.ci_green, report.failures
    assert report.nonvacuous
    assert report.mutants_killed == report.mutants_total > 0


def test_persist_gets_the_persist_pair_interruption_and_id_swap() -> None:
    predicate = {"op": "persist", "seconds": 1.0, "of": {"op": "all", "of": [INSIDE, MOVING]}}
    traces = {
        t.name: t.expected_violation for t in generate_rule_traces(_rule(predicate=predicate))
    }
    assert traces["persist_s_0.9s"] is False
    assert traces["persist_s_1.1s"] is True
    assert traces["interrupted_persist"] is False
    assert traces["id_swap_mid_persist"] is True  # 0.6 s + the 0.5 s hold reaches 1 s
    long = {**predicate, "seconds": 4.0}
    swap = {t.name: t for t in generate_rule_traces(_rule(predicate=long))}["id_swap_mid_persist"]
    assert swap.expected_violation is False


def test_a_fast_forklift_leaving_the_held_point_shortens_the_id_swap() -> None:
    fast = {**MOVING, "speed_mps_gt": 8.0}
    predicate = {"op": "persist", "seconds": 1.0, "of": {"op": "all", "of": [INSIDE, fast]}}
    rule = _rule(predicate=predicate)
    swap = {t.name: t for t in generate_rule_traces(rule)}["id_swap_mid_persist"]
    assert "leaves the first ID's held point" in swap.description
    assert swap.expected_violation is False
    assert run_rule_ci(rule).ci_green


def test_moving_on_the_subject_under_persist_skips_the_id_swap() -> None:
    moving = {**MOVING, "object": "person"}
    predicate = {"op": "persist", "seconds": 1.0, "of": {"op": "all", "of": [INSIDE, moving]}}
    names = {t.name for t in generate_rule_traces(_rule(predicate=predicate))}
    assert "id_swap_mid_persist" not in names
    assert "interrupted_persist" in names


# --- calibration --------------------------------------------------------------------------------


def test_a_real_perspective_calibration_lays_the_traces_out_through_its_homography() -> None:
    rule = _rule(calibration=None)
    traces = [
        t
        for t in generate_rule_traces(rule, calibrations={"cam1": PERSPECTIVE})
        if t.kind not in {"kinematic", "operator"}  # these use the synthetic plane by design
    ]
    assert {t.calibration for t in traces} == {PERSPECTIVE}
    zone = {t.name: t for t in traces}
    assert _ground_distance(zone["zone_inside"], 30) == pytest.approx(1.95)
    assert _ground_distance(zone["zone_outside"], 30) == pytest.approx(2.05)
    forklift = zone["zone_inside"].tracks[-1].points
    widths = {round(p.box_xyxy[2] - p.box_xyxy[0], 6) for p in forklift}
    assert len(widths) > 1  # perspective: the 0.4 m footprint spans a varying pixel width

    report = run_rule_ci(rule, calibrations={"cam1": PERSPECTIVE})
    assert report.ci_green, report.failures
    assert report.nonvacuous


def test_the_prove_hook_passes_the_runs_calibrations_through() -> None:
    plan = SitePlan(
        plan_id="p",
        site_name="Dock",
        setting_inferred="warehouse",
        languages=("en",),
        request=(),
        inventory=(),
        feasibility=(),
        rules=(_rule(calibration=None),),
        privacy_mode=PrivacyMode.STANDARD,
        acceptance=AcceptanceResult(checks=(), rounds=1),
    )
    without = {c.check: c for c in prove_plan_hook()(plan).acceptance.checks}
    assert not without["ci_green"].passed
    assert "buffer zone needs calibration" in (without["ci_green"].detail or "")

    proved = prove_plan_hook(calibrations=lambda _: {"cam1": PERSPECTIVE})(plan)
    assert all(c.passed for c in proved.acceptance.checks)


def test_a_calibration_for_another_camera_or_on_the_horizon_is_refused() -> None:
    elsewhere = synthetic_calibration("cam2")
    with pytest.raises(UnsupportedRuleError, match="buffer zone needs calibration"):
        generate_rule_traces(_rule(calibration=None), calibrations={"cam1": elsewhere})
    # Ground y = 3 m (where the forklift drives) maps to infinity in the image.
    horizon = np.linalg.inv(np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 1.0, -3.0]]))
    cal = PERSPECTIVE.model_copy(update={"homography": tuple(map(tuple, horizon.tolist()))})
    with pytest.raises(UnsupportedRuleError, match="cannot place the trace"):
        generate_rule_traces(_rule(), calibrations={"cam1": cal})


def test_calibrated_cameras_reads_the_rules_ground_plane_names() -> None:
    rule = _rule(calibration="ground-plane@cam1,ground-plane@cam3")
    assert calibrated_cameras(rule) == {"cam1", "cam3"}
    assert calibrated_cameras(_rule(calibration=None)) == frozenset()


def test_a_bent_layout_is_refused_rather_than_mis_expected() -> None:
    shape = BufferShape(
        camera_id="cam1",
        zone="forklift_buffer",
        subject="person",
        anchor="forklift",
        buffer_m=2.0,
        moving=None,
        moving_subject=False,
        persist_s=None,
    )
    scene = BufferScene(shape, GroundPlane(synthetic_calibration("cam1")))
    samples = scene.layout([Stay(1.0, 1.0)], 0.0)
    with pytest.raises(UnsupportedRuleError, match="bends the layout"):
        scene.check([replace(s, offset=3.0) for s in samples], 0.0)
    moving = BufferScene(
        replace(shape, moving=Moving(object="forklift", speed_mps_gt=0.2)), scene.plane
    )
    with pytest.raises(UnsupportedRuleError, match="m/s"):
        moving.check(moving.layout([Stay(1.0, 1.0)], 0.0), 1.0)


# --- unsupported buffer shapes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"predicate": {**INSIDE, "object": "person.hands"}}, "foot point"),
        ({"predicate": {**INSIDE, "object": "forklift"}}, "buffer of the subject itself"),
        (
            {"predicate": {"op": "all", "of": [INSIDE, {**MOVING, "object": "pallet"}]}},
            "neither 'person' nor 'forklift'",
        ),
        (
            {
                "zones": {
                    "forklift_buffer": {
                        "kind": "buffer",
                        "camera_id": "cam1",
                        "buffer_of": "forklift",
                        "buffer_m": 0.01,
                    }
                }
            },
            "needs buffer_of and buffer_m",
        ),
    ],
    ids=["hands", "self-buffer", "third-concept", "tiny-buffer"],
)
def test_unsupported_buffer_layouts_fail_honestly(update: dict[str, Any], message: str) -> None:
    raw = {**_raw(), **update}
    raw["concepts"] = {**raw["concepts"], "pallet": {"prompt": "pallet"}}
    report = run_rule_ci(RuleIR.model_validate(raw))
    assert not report.ci_green
    assert message in report.failures[0]
