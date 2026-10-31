"""Pipelined chunk segmentation in the runtime loop (P3-08): keeping up with hosted SAM latency.

Fakes only, no network. Latency is either real but scaled (`ScaledClock`: stream seconds run
`SCALE` times faster than wall time, so "4 s of SAM" is a 160 ms sleep) or gated by events, so
completion order is chosen by the test.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rulecam.bus import Topic
from rulecam.contracts import Chunk, Detection, Mask, Track
from rulecam.evidence.chain import read_chain
from rulecam.perception.masks import NO_MASK
from rulecam.perception.router import CallMeter
from rulecam.perception.tracking import SegmentedChunk, Tracker
from rulecam.replay.errors import ReplayMiss
from rulecam.rules.backtest import DecisionContext
from rulecam.runtime.loop import ActivePlan, LoopMetrics, RuntimeLoop, chunk_end, percentile
from rulecam.runtime.sampling import CameraSampler, SamplingPolicy
from tests.fakes import EPOCH, FakeClock, FakeSegmenter, InMemoryTrackStore, make_chunk
from tests.rules.helpers import make_rule
from tests.runtime.fakes import (
    EmptyTracker,
    FakeTracker,
    RecordingBus,
    WholeChunkTracker,
    make_loop,
    sequence_plan,
    sink_plan,
)

SCALE = 25.0  # stream seconds per wall second
CHUNK_S = 3.0  # make_chunk: 30 frames at 10 fps
SAM_LATENCY_S = 4.0
TIMEOUT_S = 10.0


class ScaledClock:
    """Stream time that runs `scale` times faster than the wall clock, from `start`."""

    def __init__(self, start: datetime = EPOCH, scale: float = SCALE) -> None:
        self._start = start
        self._scale = scale
        self._t0 = time.monotonic()

    def now(self) -> datetime:
        return self._start + timedelta(seconds=(time.monotonic() - self._t0) * self._scale)


class SlowTracker(WholeChunkTracker):
    """Segments with a fixed (scaled) latency; records concurrency, submission lag and order."""

    def __init__(self, clock: ScaledClock, latency_s: float = SAM_LATENCY_S) -> None:
        self.clock = clock
        self.latency_wall_s = latency_s / SCALE
        self.lock = threading.Lock()
        self.running = 0
        self.max_running = 0
        self.lags: list[float] = []
        self.stitched: list[str] = []

    def segment(self, chunk: Chunk, concepts: Iterable[str]) -> SegmentedChunk:
        with self.lock:
            self.lags.append((self.clock.now() - chunk_end(chunk)).total_seconds())
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        time.sleep(self.latency_wall_s)
        with self.lock:
            self.running -= 1
        return super().segment(chunk, concepts)

    def process_chunk(self, chunk: Chunk, concepts: Iterable[str]) -> list[Track]:
        self.stitched.append(chunk.chunk_id)
        return []


class GatedTracker(WholeChunkTracker):
    """Each chunk's segmentation waits for its gate; some chunks fail. Stitches like FakeTracker."""

    def __init__(self, store: InMemoryTrackStore, chunk_ids: Iterable[str]) -> None:
        self.inner = FakeTracker(store)
        self.gates = {chunk_id: threading.Event() for chunk_id in chunk_ids}
        self.fail: set[str] = set()
        self.lock = threading.Lock()
        self.started: list[str] = []
        self.completed: list[str] = []
        self.stitched: list[str] = []

    def segment(self, chunk: Chunk, concepts: Iterable[str]) -> SegmentedChunk:
        with self.lock:
            self.started.append(chunk.chunk_id)
        assert self.gates[chunk.chunk_id].wait(TIMEOUT_S)
        with self.lock:
            self.completed.append(chunk.chunk_id)
        if chunk.chunk_id in self.fail:
            raise ReplayMiss(provider="meta", model="sam-3.1", key=chunk.chunk_id)
        return super().segment(chunk, concepts)

    def process_chunk(self, chunk: Chunk, concepts: Iterable[str]) -> list[Track]:
        self.stitched.append(chunk.chunk_id)
        return self.inner.process_chunk(chunk, concepts)

    def release(self, *indices: int) -> None:
        for index in indices:
            self.gates[make_chunk(index=index).chunk_id].set()


async def wait_until(condition: Callable[[], bool], timeout_s: float = TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.002)


def ended(clock: ScaledClock, chunk: Chunk) -> Callable[[], bool]:
    return lambda: clock.now() >= chunk_end(chunk)


def has_len(items: list[str], n: int) -> Callable[[], bool]:
    return lambda: len(items) == n


@contextlib.asynccontextmanager
async def running(loop: RuntimeLoop, bus: RecordingBus) -> AsyncIterator[None]:
    bus.bind_loop(asyncio.get_running_loop())
    task = asyncio.create_task(loop.run())
    await wait_until(lambda: bus.subscriber_count(Topic.CHUNK_READY) > 0)
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def consumed(metrics: LoopMetrics) -> int:
    """Chunks the loop has decided about (processed or skipped for any reason)."""
    return (
        metrics.chunks_processed
        + metrics.chunks_skipped
        + metrics.chunks_skipped_idle
        + metrics.chunks_skipped_still
        + metrics.chunks_skipped_budget
        + metrics.chunks_skipped_no_rule
    )


async def publish_live(bus: RecordingBus, clock: ScaledClock, n: int) -> None:
    """Publish chunks 0..n-1 of cam1 as a camera would: each one when it ends."""
    for i in range(n):
        chunk = make_chunk(index=i)
        await wait_until(ended(clock, chunk))
        bus.publish(Topic.CHUNK_READY, chunk)


# --- keeping up --------------------------------------------------------------------------------


async def test_depth_4_keeps_up_with_4_s_sam_on_3_s_chunks(tmp_path: Path) -> None:
    clock = ScaledClock()
    tracker = SlowTracker(clock)
    loop, bus, _, _ = make_loop(tmp_path, clock, tracker=tracker, pipeline_depth=4)  # type: ignore[arg-type]
    loop.set_plan(sink_plan())
    n = 12
    async with running(loop, bus):
        await publish_live(bus, clock, n)
        await wait_until(lambda: len(tracker.stitched) == n)
        metrics = loop.metrics
    # Every chunk segmented and consumed in order; the loop never fell behind.
    assert tracker.stitched == [make_chunk(index=i).chunk_id for i in range(n)]
    assert metrics.chunks_processed == n
    assert metrics.chunks_skipped == 0
    assert not metrics.keyframe_mode
    assert max(tracker.lags) < 6.0  # submitted on arrival (max_lag_s is 6 s)
    assert metrics.lag_s < 6.0
    assert tracker.max_running >= 2  # 4 s of SAM per 3 s chunk needs two in flight
    assert metrics.pipeline_depth == 4
    assert SAM_LATENCY_S <= metrics.segment_latency_p50_s <= metrics.segment_latency_p95_s
    assert metrics.segment_latency_p95_s < 3 * SAM_LATENCY_S


async def test_depth_1_falls_behind_like_the_sequential_loop(tmp_path: Path) -> None:
    clock = ScaledClock()
    tracker = SlowTracker(clock)
    loop, bus, _, _ = make_loop(tmp_path, clock, tracker=tracker, pipeline_depth=1)  # type: ignore[arg-type]
    loop.set_plan(sink_plan())
    n = 12
    async with running(loop, bus):
        await publish_live(bus, clock, n)
        await wait_until(lambda: consumed(loop.metrics) == n)
        metrics = loop.metrics
    assert tracker.max_running == 1
    assert metrics.chunks_skipped > 0  # lag passed 6 s, keyframe mode thinned the chunks
    assert max(tracker.lags) > 6.0
    assert tracker.stitched == sorted(tracker.stitched)


# --- ordering, back-pressure, failures ---------------------------------------------------------


async def test_results_are_consumed_in_chunk_order_whatever_order_they_complete(
    tmp_path: Path,
) -> None:
    store = InMemoryTrackStore()
    ids = [make_chunk(index=i).chunk_id for i in range(5)]
    tracker = GatedTracker(store, ids)
    loop, bus, chain, _ = make_loop(
        tmp_path,
        FakeClock(),
        tracker=tracker,
        store=store,
        pipeline_depth=4,
    )
    loop.set_plan(sink_plan())
    async with running(loop, bus):
        for i in range(5):
            bus.publish(Topic.CHUNK_READY, make_chunk(index=i))
        await wait_until(lambda: len(tracker.started) == 4)
        await asyncio.sleep(0.05)
        # The pipeline is full: chunk 4 waits for a slot instead of queueing a fifth request.
        assert tracker.started == ids[:4]
        assert loop.metrics.in_flight == 4
        for k, index in enumerate((3, 1, 2), start=1):
            tracker.release(index)
            await wait_until(has_len(tracker.completed, k))
        await asyncio.sleep(0.05)
        assert tracker.stitched == []  # chunk 0 is still out: nothing is consumed out of order
        tracker.release(0, 4)
        await wait_until(lambda: len(tracker.stitched) == 5 and loop.metrics.in_flight == 0)
    assert tracker.completed[:4] == [ids[3], ids[1], ids[2], ids[0]]
    assert tracker.stitched == ids
    assert loop.metrics.chunks_processed == 5
    records = read_chain(chain.path_for(FakeClock().now().date()))
    windows = [
        DecisionContext.model_validate(r.payload["context"]).t1
        for r in records
        if r.kind == "candidate"
    ]
    assert windows == sorted(windows)


async def test_a_failed_chunk_does_not_block_the_ones_behind_it(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    ids = [make_chunk(index=i).chunk_id for i in range(4)]
    tracker = GatedTracker(store, ids)
    tracker.fail = {ids[1]}
    loop, bus, _, _ = make_loop(
        tmp_path,
        FakeClock(),
        tracker=tracker,
        store=store,
        pipeline_depth=4,
    )
    loop.set_plan(sink_plan())
    async with running(loop, bus):
        for i in range(4):
            bus.publish(Topic.CHUNK_READY, make_chunk(index=i))
        await wait_until(lambda: len(tracker.started) == 4)
        tracker.release(1, 2, 3, 0)
        await wait_until(lambda: len(tracker.stitched) == 3 and loop.metrics.in_flight == 0)
        metrics = loop.metrics
    assert tracker.stitched == [ids[0], ids[2], ids[3]]
    assert metrics.chunks_failed == 1
    assert metrics.chunks_processed == 4  # counted as before: a failed chunk was attempted


def test_a_failed_chunk_is_a_coverage_gap(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    tracker = GatedTracker(store, [make_chunk(index=i).chunk_id for i in range(2)])
    tracker.release(0, 1)
    tracker.fail = {make_chunk(index=0).chunk_id}
    loop, _, _, _ = make_loop(tmp_path, FakeClock(), tracker=tracker, store=store)
    loop.set_plan(sequence_plan())
    with pytest.raises(ReplayMiss):
        loop.handle_chunk(make_chunk(index=0))
    loop.handle_chunk(make_chunk(index=1))
    # The sequence rule needs its whole window; chunk 0 was never observed, so it is blind.
    assert loop.metrics.windows_blind == 1


# --- budget ------------------------------------------------------------------------------------


async def test_the_budget_counts_chunks_still_in_flight(tmp_path: Path) -> None:
    gates: dict[str, threading.Event] = {
        make_chunk("warehouse", i).chunk_id: threading.Event() for i in range(4)
    }

    def blocked(chunk: Chunk, concept: str) -> list[Detection]:
        assert gates[chunk.chunk_id].wait(TIMEOUT_S)
        return []

    segmenter = FakeSegmenter(responder=blocked)
    meter = CallMeter(segmenter)
    loop, bus, _, _ = make_loop(
        tmp_path,
        FakeClock(),
        tracker=Tracker(meter, reprompt=False, max_workers=2),
        usage=meter,
        sampling=SamplingPolicy(budget_frames_per_min=200, budget_stride=3),
        pipeline_depth=4,
    )
    rule = make_rule(
        {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0},
        concepts={"person": "person", "forklift": "forklift"},
        severity="medium",
    )
    loop.set_plan(ActivePlan(rules=(rule,)))
    async with running(loop, bus):
        for i in range(4):
            bus.publish(Topic.CHUNK_READY, make_chunk("warehouse", i))
        # Two concepts, 50 billed frames each: chunks 0 and 1 reserve the whole budget, so 2
        # and 3 are degraded before a single bill has come back.
        await wait_until(lambda: loop.metrics.chunks_skipped_budget == 2)
        assert {call.chunk_id for call in segmenter.calls} == {
            make_chunk("warehouse", 0).chunk_id,
            make_chunk("warehouse", 1).chunk_id,
        }
        assert loop.metrics.billed_frames == 0
        assert loop.metrics.budget_degraded == ("warehouse",)
        for gate in gates.values():
            gate.set()
        await wait_until(lambda: loop.metrics.chunks_processed == 2 and loop.metrics.in_flight == 0)
        metrics = loop.metrics
    assert (metrics.sam_calls, metrics.billed_frames) == (4, 200)
    assert metrics.billed_frames_per_min == {"warehouse": 200}
    assert meter.take_chunk("warehouse", make_chunk("warehouse", 0).chunk_id) == (0, 0)


def test_reservations_count_against_the_budget_until_recorded() -> None:
    sampler = CameraSampler(SamplingPolicy(budget_frames_per_min=100, budget_stride=3))
    rules = [make_rule({"op": "count", "object": "person", "lt": 1}, severity="medium")]
    first, second = make_chunk("cam1", 0), make_chunk("cam1", 1)
    assert sampler.admit(first, rules, moving=True) == "run"
    sampler.reserve(first, 100)
    sampler.reserve(second, 0)  # nothing to reserve: ignored
    assert sampler.in_flight_frames("cam1") == 100
    assert sampler.in_flight_frames("nope") == 0
    assert sampler.admit(second, rules, moving=True) == "budget"
    sampler.record(first, billed_frames=0, active=False)  # it failed before billing
    assert sampler.in_flight_frames("cam1") == 0
    assert sampler.admit(make_chunk("cam1", 2), rules, moving=True) == "run"


# --- depth 1 is the old loop -------------------------------------------------------------------


@pytest.mark.parametrize("tracker_type", [FakeTracker, EmptyTracker])
async def test_depth_1_matches_the_sequential_loop(tmp_path: Path, tracker_type: type) -> None:
    n = 9
    policy = SamplingPolicy(idle_stride_medium=3, hot_holdoff_s=4.0)

    def build(folder: str) -> tuple[RuntimeLoop, RecordingBus, Path]:
        store = InMemoryTrackStore()
        tracker = tracker_type(store) if tracker_type is FakeTracker else tracker_type()
        loop, bus, chain, _ = make_loop(
            tmp_path / folder, FakeClock(), tracker=tracker, store=store, sampling=policy
        )
        loop.set_plan(sink_plan())
        return loop, bus, chain.path_for(FakeClock().now().date())

    sequential, _, seq_path = build("seq")
    for i in range(n):
        sequential.handle_chunk(make_chunk(index=i))

    piped, bus, piped_path = build("piped")
    async with running(piped, bus):
        for i in range(n):
            bus.publish(Topic.CHUNK_READY, make_chunk(index=i))
        await wait_until(lambda: consumed(piped.metrics) == n and piped.metrics.in_flight == 0)
        await asyncio.sleep(0.05)

    def kinds(path: Path) -> list[str]:
        return [r.kind for r in read_chain(path)] if path.is_file() else []

    latency = {"segment_latency_p50_s", "segment_latency_p95_s", "stages"}  # timings differ
    assert piped.metrics.model_dump(exclude=latency) == sequential.metrics.model_dump(
        exclude=latency
    )
    assert kinds(piped_path) == kinds(seq_path)


# --- metrics and pieces ------------------------------------------------------------------------


def test_metrics_report_the_pipeline() -> None:
    fields = set(LoopMetrics().model_dump())
    assert {
        "in_flight",
        "pipeline_depth",
        "segment_latency_p50_s",
        "segment_latency_p95_s",
    } <= fields
    assert percentile([], 50) == 0
    samples = [float(i) for i in range(1, 21)]
    assert percentile(samples, 50) == 10
    assert percentile(samples, 95) == 19
    assert percentile([7.0], 95) == 7


def test_lag_is_tracked_per_camera(tmp_path: Path) -> None:
    clock = FakeClock()
    loop, _, _, _ = make_loop(tmp_path, clock, max_lag_s=6.0)
    loop.set_plan(sink_plan())
    clock.advance(60)
    loop.handle_chunk(make_chunk("late", 0))
    loop.handle_chunk(make_chunk("live", 19))
    metrics = loop.metrics
    assert metrics.keyframe_mode  # "late" is behind
    assert metrics.lag_s > 50


def test_segment_then_stitch_equals_process_chunk() -> None:
    chunk = make_chunk("cam1")
    det = Detection(
        camera_id="cam1",
        frame_idx=0,
        ts=chunk.start_ts,
        concept="person",
        object_id=1,
        mask=Mask(rle=NO_MASK, box_xyxy=(0, 0, 50, 50), confidence=0.9),
    )
    ids = iter(f"{i:08x}" for i in range(10))
    split = Tracker(FakeSegmenter({"person": [det]}), new_suffix=lambda: next(ids))
    segmented = split.segment(chunk, ["person", "person"])
    assert segmented.concepts == ("person",)
    assert segmented.detections == {"person": (det,)}
    ids2 = iter(f"{i:08x}" for i in range(10))
    whole = Tracker(FakeSegmenter({"person": [det]}), new_suffix=lambda: next(ids2))
    assert split.stitch(chunk, segmented) == whole.process_chunk(chunk, ["person"])


def test_call_meter_counts_per_chunk_and_forgets_old_chunks() -> None:
    meter = CallMeter(FakeSegmenter(), max_open_chunks=2)
    for i in range(3):
        meter.segment(make_chunk("cam1", i), "person")
    meter.segment(make_chunk("cam1", 2), "forklift")
    assert meter.take_chunk("cam1", make_chunk("cam1", 0).chunk_id) == (0, 0)  # evicted
    assert meter.take_chunk("cam1", make_chunk("cam1", 2).chunk_id) == (2, 100)
    assert meter.take_chunk("cam1", make_chunk("cam1", 2).chunk_id) == (0, 0)  # taken
    assert meter.totals("cam1") == (4, 200)
