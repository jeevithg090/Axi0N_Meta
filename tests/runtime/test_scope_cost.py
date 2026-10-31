"""Per-camera rule scope and SAM cost control in the runtime loop (P3-08, P1-03)."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pytest

from rulecam.config import Settings
from rulecam.contracts import Detection, FeasibilityVerdict, Mask, Probe, RuleIR
from rulecam.perception.masks import NO_MASK
from rulecam.perception.motion_gate import GrayFrame, MotionGate
from rulecam.perception.router import CallMeter
from rulecam.perception.static_cache import StaticServedSegmenter
from rulecam.perception.tracking import Tracker
from rulecam.planner.scope import rule_scope, rule_verdicts
from rulecam.runtime.activate import active_plan_from
from rulecam.runtime.loop import (
    ActivePlan,
    LoopMetrics,
    RuntimeLoop,
    chunk_end,
    history_s,
    rule_prompts,
)
from rulecam.runtime.sampling import CameraSampler, SamplingPolicy
from tests.fakes import FakeClock, FakeSegmenter, InMemoryTrackStore, make_chunk
from tests.rules.helpers import CAL, SINK_ZONE, make_rule
from tests.runtime.activation_helpers import checks_for, zoned_plan
from tests.runtime.fakes import EmptyTracker, FakeTracker, make_loop

WAREHOUSE, KITCHEN = "warehouse", "kitchen"
CONCERN = "keep people 2 m away from a moving forklift"


def forklift_rule(severity: str = "high", text: str = CONCERN) -> RuleIR:
    rule = make_rule(
        {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0},
        concepts={"person": "person", "forklift": "forklift"},
        rule_id="forklift-proximity",
        severity=severity,
    )
    source = rule.source.model_copy(update={"text": text})
    return rule.model_copy(update={"source": source})


def probe(concept: str, camera_id: str, lb: float = 0.9) -> Probe:
    return Probe(
        concept=concept,
        camera_id=camera_id,
        n_frames=12,
        detect_rate=lb,
        detect_lb=lb,
        median_px_height=80.0,
    )


def verdict(
    verdict: Literal["observable", "caveat", "not_observable"],
    camera_id: str,
    concern: str = CONCERN,
    concepts: tuple[str, ...] = (),
) -> FeasibilityVerdict:
    return FeasibilityVerdict(
        concern=concern,
        verdict=verdict,
        says="...",
        evidence=tuple(probe(c, camera_id) for c in concepts or ("person", "forklift")),
    )


def forklift_plan(*verdicts: FeasibilityVerdict, rule: RuleIR | None = None) -> ActivePlan:
    rule = rule or forklift_rule()
    site = zoned_plan([rule], checks_for(rule.id))
    site = site.model_copy(update={"feasibility": verdicts})
    return active_plan_from(site).active_plan


def at_end(clock: FakeClock, camera_id: str, index: int) -> None:
    clock.advance((chunk_end(make_chunk(camera_id, index)) - clock.now()).total_seconds())


def drive(
    loop: RuntimeLoop, clock: FakeClock, indices: range, camera_id: str = WAREHOUSE
) -> list[int]:
    """Feed chunks in real time; return the indices whose tracker ran."""
    tracker = loop._tracker
    assert isinstance(tracker, EmptyTracker | FakeTracker | Tracker)
    ran = []
    for i in indices:
        at_end(clock, camera_id, i)
        before = loop.metrics.chunks_processed
        loop.handle_chunk(make_chunk(camera_id, i))
        if loop.metrics.chunks_processed > before:
            ran.append(i)
    return ran


def make_detection(
    concept: str, object_id: int, box: tuple[float, float, float, float] = (0, 0, 50, 50)
) -> Detection:
    chunk = make_chunk("cam1")
    return Detection(
        camera_id="cam1",
        frame_idx=0,
        ts=chunk.start_ts,
        concept=concept,
        object_id=object_id,
        mask=Mask(rle=NO_MASK, box_xyxy=box, confidence=0.9),
    )


# --- camera scope ------------------------------------------------------------------------------


def test_activation_scopes_the_rule_to_the_cameras_that_can_see_it() -> None:
    plan = forklift_plan(verdict("observable", WAREHOUSE))
    assert plan.rule_cameras == {"forklift-proximity": (WAREHOUSE,)}
    assert [r.id for r in plan.rules_on(WAREHOUSE)] == ["forklift-proximity"]
    assert plan.rules_on(KITCHEN) == ()


def test_scoping_skips_the_kitchen_camera_entirely(tmp_path: Path) -> None:
    clock = FakeClock()
    tracker = EmptyTracker()
    loop, _, _, _ = make_loop(tmp_path, clock, tracker=tracker)
    loop.set_plan(forklift_plan(verdict("observable", WAREHOUSE)))
    for i in range(3):
        at_end(clock, KITCHEN, i)
        assert loop.handle_chunk(make_chunk(KITCHEN, i)) == []
    assert tracker.calls == []
    assert loop.metrics.chunks_skipped_no_rule == 3
    at_end(clock, WAREHOUSE, 3)
    loop.handle_chunk(make_chunk(WAREHOUSE, 3))
    assert [c for _, c in tracker.calls] == [["forklift", "person"]]


def test_not_observable_evidence_does_not_scope_and_caveat_does() -> None:
    plan = forklift_plan(verdict("caveat", WAREHOUSE), verdict("not_observable", KITCHEN))
    assert plan.rule_cameras["forklift-proximity"] == (WAREHOUSE,)


def test_verdicts_match_by_concepts_when_the_text_differs() -> None:
    rule = forklift_rule(text="forklift rule")
    kitchen_person = verdict("observable", KITCHEN, "no one at the sink", concepts=("person",))
    plan = forklift_plan(verdict("observable", WAREHOUSE), kitchen_person, rule=rule)
    assert plan.rule_cameras["forklift-proximity"] == (WAREHOUSE,)


def test_zone_cameras_scope_a_rule_too() -> None:
    rule = make_rule(
        {"op": "inside", "object": "person", "zone": "sink_zone"},
        zones={"sink_zone": {"kind": "polygon", "camera_id": "cam1", "polygon_px": [[0, 0]] * 3}},
    )
    site = zoned_plan([rule], checks_for(rule.id))
    assert rule_scope(site.feasibility, rule) == ("cam1",)
    assert rule_verdicts(site.feasibility, rule) == []


def test_a_rule_without_camera_evidence_runs_everywhere(tmp_path: Path) -> None:
    plan = forklift_plan()
    assert plan.rule_cameras == {}
    clock = FakeClock()
    tracker = EmptyTracker()
    loop, _, _, _ = make_loop(tmp_path, clock, tracker=tracker)
    loop.set_plan(plan)
    at_end(clock, KITCHEN, 0)
    loop.handle_chunk(make_chunk(KITCHEN, 0))
    assert len(tracker.calls) == 1


def test_old_persisted_plans_without_scope_still_load() -> None:
    raw = ActivePlan(rules=(forklift_rule(),)).model_dump(mode="json", exclude={"rule_cameras"})
    assert ActivePlan.model_validate(raw).rule_cameras == {}


# --- one perception call per chunk per camera --------------------------------------------------


def metered_loop(
    tmp_path: Path, clock: FakeClock, segmenter: FakeSegmenter, **kwargs: Any
) -> tuple[RuntimeLoop, CallMeter]:
    meter = CallMeter(segmenter)
    served = StaticServedSegmenter(meter)
    tracker = Tracker(served, max_workers=4)
    loop, _, _, _ = make_loop(
        tmp_path,
        clock,
        tracker=tracker,
        usage=meter,
        static_sink=lambda c, v: served.set_static(c, generation=v),
        **kwargs,
    )
    return loop, meter


def test_one_chunk_segments_both_concepts_once_each_in_parallel(tmp_path: Path) -> None:
    clock = FakeClock()
    segmenter = FakeSegmenter()
    loop, meter = metered_loop(tmp_path, clock, segmenter)
    loop.set_plan(forklift_plan(verdict("observable", WAREHOUSE)))
    drive(loop, clock, range(1))
    # The hosted API takes one concept per request: both concepts, one request each.
    assert sorted(c.concept for c in segmenter.calls) == ["forklift", "person"]
    assert meter.totals(WAREHOUSE) == (2, 100)  # 30-frame chunks bill the 50-frame minimum
    assert (loop.metrics.sam_calls, loop.metrics.billed_frames) == (2, 100)
    assert loop.metrics.billed_frames_per_min == {WAREHOUSE: 100}


def test_only_the_sam_prompt_is_segmented_not_the_concept_key() -> None:
    rule = make_rule(
        {"op": "inside", "object": "worker", "zone": "sink_zone"},
        concepts={"worker": "person"},
    )
    assert rule_prompts(rule, {"sink_zone": SINK_ZONE}) == {"person": False}


def test_static_concepts_come_from_the_cache(tmp_path: Path) -> None:
    clock = FakeClock()
    sink = make_detection("sink", 7)
    segmenter = FakeSegmenter({"sink": [sink]})
    loop, _ = metered_loop(tmp_path, clock, segmenter)
    rule = make_rule(
        {"op": "inside", "object": "person", "zone": "sink_buffer"},
        concepts={"person": "person", "sink": "sink"},
        zones={
            "sink_buffer": {
                "kind": "buffer",
                "camera_id": "cam1",
                "buffer_of": "sink",
                "buffer_m": 1.0,
            }
        },
    )
    rule = rule.model_copy(
        update={
            "concepts": {
                **rule.concepts,
                "sink": rule.concepts["sink"].model_copy(update={"static": True}),
            }
        }
    )
    loop.set_plan(ActivePlan(rules=(rule,), calibration=CAL, plan_version_hash="v1"))
    drive(loop, clock, range(3), camera_id="cam1")
    assert [c.concept for c in segmenter.calls].count("sink") == 1
    assert [c.concept for c in segmenter.calls].count("person") == 3


def test_static_served_segmenter_spreads_cached_objects_and_ignores_reprompts() -> None:
    sink = make_detection("sink", 7)
    inner = FakeSegmenter({"sink": [sink], "person": []})
    served = StaticServedSegmenter(inner)
    served.set_static({"sink"}, generation="v1")
    chunk = make_chunk(n_frames=5)
    detections = served.segment(chunk, "sink")
    assert [d.frame_idx for d in detections] == [0, 1, 2, 3, 4]
    assert served.segment(chunk, "sink", box_prompts={1: (0.0, 0.0, 1.0, 1.0)}) == []
    served.segment(chunk, "person")
    assert [c.concept for c in inner.calls] == ["sink", "person"]


# --- motion gate before any call ---------------------------------------------------------------


def still_frames() -> list[GrayFrame]:
    return [np.zeros((90, 160), dtype=np.uint8) for _ in range(3)]


def moving_frames() -> list[GrayFrame]:
    frames = []
    for i in range(3):
        frame = np.zeros((90, 160), dtype=np.uint8)
        frame[10:40, 10 + 30 * i : 40 + 30 * i] = 255
        frames.append(frame)
    return frames


def gate_with(moving: set[int], camera_id: str = WAREHOUSE) -> MotionGate:
    def read(path: str) -> list[GrayFrame]:
        index = int(Path(path).stem.rsplit("_", 1)[1])
        return moving_frames() if index in moving else still_frames()

    del camera_id
    return MotionGate(read_frames=read)


def test_no_motion_means_no_sam_call(tmp_path: Path) -> None:
    clock = FakeClock()
    segmenter = FakeSegmenter()
    loop, _ = metered_loop(tmp_path, clock, segmenter, motion_gate=gate_with(set()))
    loop.set_plan(forklift_plan(verdict("observable", WAREHOUSE), rule=forklift_rule("medium")))
    ran = drive(loop, clock, range(6))
    assert ran == [0]  # the first chunk is the gate's keyframe; still chunks after it are skipped
    assert loop.metrics.chunks_skipped_still == 5
    assert len(segmenter.calls) == 2


def test_motion_lets_the_chunk_through(tmp_path: Path) -> None:
    clock = FakeClock()
    loop, _ = metered_loop(tmp_path, clock, FakeSegmenter(), motion_gate=gate_with({2, 3}))
    loop.set_plan(forklift_plan(verdict("observable", WAREHOUSE), rule=forklift_rule("medium")))
    # 4 runs too: its first frame differs from 3's last (motion across the chunk boundary).
    assert drive(loop, clock, range(6)) == [0, 2, 3, 4]


def test_unreadable_chunks_count_as_moving(tmp_path: Path) -> None:
    clock = FakeClock()
    tracker = EmptyTracker()
    loop, _, _, _ = make_loop(tmp_path, clock, tracker=tracker, motion_gate=MotionGate())
    loop.set_plan(forklift_plan(verdict("observable", WAREHOUSE), rule=forklift_rule("medium")))
    assert drive(loop, clock, range(2)) == [0, 1]  # the files do not exist: never "still"


# --- idle sampling -----------------------------------------------------------------------------

IDLE = SamplingPolicy(idle_stride_low=3, idle_stride_medium=3, idle_stride_high=2)


def test_idle_cameras_sample_every_third_chunk(tmp_path: Path) -> None:
    clock = FakeClock()
    loop, _, _, _ = make_loop(tmp_path, clock, tracker=EmptyTracker(), sampling=IDLE)
    loop.set_plan(forklift_plan(verdict("observable", WAREHOUSE), rule=forklift_rule("medium")))
    assert drive(loop, clock, range(9)) == [0, 3, 6]
    assert loop.metrics.chunks_skipped_idle == 6


def test_a_candidate_switches_the_camera_to_every_chunk(tmp_path: Path) -> None:
    clock = FakeClock()
    store = InMemoryTrackStore()
    policy = IDLE.model_copy(update={"hot_holdoff_s": 9.0})
    loop, _, _, _ = make_loop(
        tmp_path, clock, tracker=FakeTracker(store), store=store, sampling=policy
    )
    rule = make_rule({"op": "inside", "object": "person", "zone": "sink_zone"})
    loop.set_plan(ActivePlan(rules=(rule,), zones={"sink_zone": SINK_ZONE}, calibration=CAL))
    assert drive(loop, clock, range(4), camera_id="cam1") == [0, 1, 2, 3]
    assert loop.metrics.candidates >= 1
    assert loop.metrics.chunks_skipped_idle == 0


def test_the_camera_goes_idle_again_after_the_holdoff() -> None:
    sampler = CameraSampler(IDLE.model_copy(update={"hot_holdoff_s": 3.0}))
    rules = [forklift_rule("medium")]
    runs = []
    for i in range(8):
        chunk = make_chunk(WAREHOUSE, i)
        if sampler.admit(chunk, rules, moving=True) == "run":
            runs.append(i)
            sampler.record(chunk, billed_frames=0, active=i == 0)
    assert runs == [
        0,
        1,
        2,
        5,
    ]  # hot until 3 s after chunk 0 ends (covers chunk 1 start), then 1 in 3


def test_high_severity_samples_every_second_chunk_when_still(tmp_path: Path) -> None:
    clock = FakeClock()
    tracker = EmptyTracker()
    loop, _, _, _ = make_loop(
        tmp_path, clock, tracker=tracker, sampling=IDLE, motion_gate=gate_with(set())
    )
    loop.set_plan(forklift_plan(verdict("observable", WAREHOUSE)))
    assert drive(loop, clock, range(6)) == [0, 2, 4]


def test_high_severity_switches_to_every_chunk_on_motion(tmp_path: Path) -> None:
    clock = FakeClock()
    policy = IDLE.model_copy(update={"hot_holdoff_s": 6.0})
    loop, _, _, _ = make_loop(
        tmp_path, clock, tracker=EmptyTracker(), sampling=policy, motion_gate=gate_with({3})
    )
    loop.set_plan(forklift_plan(verdict("observable", WAREHOUSE)))
    # 3 has motion (and 4, across the boundary): run at once, then every chunk until 6 s after
    # chunk 4 ends (chunk 7 starts then), then every 2nd chunk again.
    assert drive(loop, clock, range(11)) == [0, 2, 3, 4, 5, 6, 7, 9]


def test_idle_stride_is_the_strictest_severity_on_the_camera() -> None:
    assert IDLE.idle_stride([forklift_rule("low"), forklift_rule("high")]) == 2
    assert IDLE.idle_stride([]) == 1
    with pytest.raises(ValueError, match="less than or equal to 2"):
        SamplingPolicy(idle_stride_high=3)


def test_policy_from_settings() -> None:
    settings = Settings(_env_file=None, rulecam_sam_budget_frames_per_min=500)  # type: ignore[call-arg,unused-ignore]
    policy = SamplingPolicy.from_settings(settings)
    assert (policy.idle_stride_medium, policy.idle_stride_high) == (3, 2)
    assert policy.budget_frames_per_min == 500


# --- budget ------------------------------------------------------------------------------------


def test_over_budget_degrades_to_keyframes_instead_of_falling_behind(tmp_path: Path) -> None:
    clock = FakeClock()
    policy = SamplingPolicy(budget_frames_per_min=200, budget_stride=3)
    loop, _ = metered_loop(tmp_path, clock, FakeSegmenter(), sampling=policy)
    loop.set_plan(forklift_plan(verdict("observable", WAREHOUSE), rule=forklift_rule("medium")))
    ran = drive(loop, clock, range(12))
    # 100 frames per chunk: after two chunks the minute holds 200, so one chunk in three runs.
    assert ran[:3] == [0, 1, 4]
    metrics = loop.metrics
    assert metrics.budget_exceeded >= 1
    assert metrics.chunks_skipped_budget >= 4
    assert metrics.lag_s == 0
    assert not metrics.keyframe_mode


def test_budget_recovers_when_the_minute_drains() -> None:
    sampler = CameraSampler(SamplingPolicy(budget_frames_per_min=100, budget_stride=10))
    rules = [forklift_rule("medium")]
    first = make_chunk(WAREHOUSE, 0)
    assert sampler.admit(first, rules, moving=True) == "run"
    sampler.record(first, billed_frames=100, active=False)
    assert sampler.admit(make_chunk(WAREHOUSE, 1), rules, moving=True) == "budget"
    assert sampler.degraded() == [WAREHOUSE]
    assert sampler.admit(make_chunk(WAREHOUSE, 25), rules, moving=True) == "run"
    assert sampler.degraded() == []
    assert sampler.budget_exceeded == 1


def test_budget_never_thins_an_active_high_severity_camera() -> None:
    sampler = CameraSampler(SamplingPolicy(budget_frames_per_min=50, budget_stride=5))
    rules = [forklift_rule("high")]
    for i in range(4):
        chunk = make_chunk(WAREHOUSE, i)
        assert sampler.admit(chunk, rules, moving=True) == "run"
        sampler.record(chunk, billed_frames=100, active=True)
    assert sampler.billed_last_minute(WAREHOUSE, chunk_end(chunk)) == 400
    assert sampler.billed_last_minute("nope", chunk_end(chunk)) == 0


# --- metrics -----------------------------------------------------------------------------------


def test_metrics_keep_the_old_fields_and_add_the_cost_counters() -> None:
    fields = set(LoopMetrics().model_dump())
    old = {
        "chunks_processed",
        "chunks_skipped",
        "lag_s",
        "keyframe_mode",
        "candidates",
        "windows_blind",
        "chunks_failed",
    }
    new = {
        "billed_frames",
        "sam_calls",
        "chunks_skipped_no_rule",
        "chunks_skipped_idle",
        "chunks_skipped_still",
        "chunks_skipped_budget",
        "billed_frames_per_min",
        "budget_exceeded",
        "budget_degraded",
    }
    assert old | new <= fields


def test_history_needed_by_each_temporal_operator() -> None:
    inside: dict[str, object] = {"op": "inside", "object": "person", "zone": "z"}
    assert history_s(make_rule(inside)) == 0
    assert history_s(make_rule({"op": "persist", "of": inside, "seconds": 4})) == 4
    assert history_s(make_rule({"op": "absence", "object": "p", "zone": "z", "seconds": 9})) == 9
    dwell = {"op": "dwell", "object": "p", "zone": "z", "total_s_gte": 20}
    assert history_s(make_rule(dwell)) == 20
    assert history_s(make_rule({"op": "rate", "of": inside, "per_minute_gt": 2})) == 60


def test_call_meter_counts_each_hosted_request_of_a_reprompt() -> None:
    meter = CallMeter(FakeSegmenter())
    chunk = make_chunk(WAREHOUSE, n_frames=60)
    boxes = dict.fromkeys(range(1, 18), (0.0, 0.0, 1.0, 1.0))  # 17 boxes: two requests
    meter.segment(chunk, "person", box_prompts=boxes)
    assert meter.totals(WAREHOUSE) == (2, 120)
    assert meter.totals(KITCHEN) == (0, 0)


def test_parallel_tracking_keeps_concept_order() -> None:
    segmenter = FakeSegmenter(
        {"person": [make_detection("person", 1)], "forklift": [make_detection("forklift", 2)]}
    )
    tracker = Tracker(segmenter, max_workers=2)
    tracks = tracker.process_chunk(make_chunk("cam1"), ["person", "forklift", "person"])
    assert [t.concept for t in tracks] == ["person", "forklift"]


async def test_run_keeps_companions_alive_and_cancels_them(tmp_path: Path) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def companion() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    loop, bus, _, _ = make_loop(tmp_path, FakeClock(), companions=(companion,))
    bus.bind_loop(asyncio.get_running_loop())
    task = asyncio.create_task(loop.run())
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await asyncio.wait_for(cancelled.wait(), 1)


def test_a_camera_with_only_static_concepts_is_still_processed(tmp_path: Path) -> None:
    clock = FakeClock()
    tracker = EmptyTracker()
    loop, _, _, _ = make_loop(tmp_path, clock, tracker=tracker, motion_gate=gate_with(set()))
    rule = make_rule({"op": "count", "object": "rack", "lt": 1}, concepts={"rack": "rack"})
    rack = rule.concepts["rack"].model_copy(update={"static": True})
    loop.set_plan(ActivePlan(rules=(rule.model_copy(update={"concepts": {"rack": rack}}),)))
    assert drive(loop, clock, range(3), camera_id="cam1") == [0, 1, 2]
    assert loop.metrics.chunks_skipped_still == 0
