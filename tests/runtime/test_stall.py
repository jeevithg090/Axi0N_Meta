"""The runtime stall fix (P3-08): verification off the in-order path, separate executors, timings.

Fakes only, no network. Before the fix, Tier 1 verification ran inside the in-order stage under
the loop's state lock, and every blocking step shared asyncio's default executor, so one slow
verdict (or a busy default executor) held up every chunk of every camera.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from rulecam.bus import Topic
from rulecam.contracts import CandidateEvent, Chunk, RuleIR, Track, VerifierVerdict
from rulecam.evidence.chain import read_chain
from rulecam.perception.tracking import SegmentedChunk
from rulecam.rules.backtest import DecisionContext
from rulecam.runtime.loop import ActivePlan, RuntimeLoop
from tests.fakes import FakeClock, InMemoryTrackStore, make_chunk, make_verdict
from tests.rules.helpers import CAL, INSIDE_M, SINK_ZONE, make_rule, walk_track
from tests.runtime.fakes import FakeStrips, RecordingBus, WholeChunkTracker, make_loop, sink_plan
from tests.runtime.test_pipeline import (
    SAM_LATENCY_S,
    SCALE,
    TIMEOUT_S,
    ScaledClock,
    publish_live,
    running,
    wait_until,
)

VERIFY_S = 10.0  # Glimmer's measured Tier 1 latency, in stream seconds


class CameraTracker(WholeChunkTracker):
    """A person in the sink zone of any camera; a new subject per chunk when `fresh`.

    Records the thread each step ran on, and optionally sleeps (wall seconds) in `segment`.
    """

    def __init__(self, store: InMemoryTrackStore, *, fresh: bool = False, sleep_s: float = 0.0):
        self.store = store
        self.fresh = fresh
        self.sleep_s = sleep_s
        self.threads: dict[str, set[str]] = {"segment": set(), "stitch": set()}
        self.stitched: list[str] = []

    def segment(self, chunk: Chunk, concepts: Iterable[str]) -> SegmentedChunk:
        self.threads["segment"].add(threading.current_thread().name)
        time.sleep(self.sleep_s)
        return super().segment(chunk, concepts)

    def process_chunk(self, chunk: Chunk, concepts: Iterable[str]) -> list[Track]:
        self.threads["stitch"].add(threading.current_thread().name)
        self.stitched.append(chunk.chunk_id)
        start_s = (chunk.start_ts - FakeClock().now()).total_seconds()
        suffix = chunk.chunk_id if self.fresh else "1"
        track = walk_track(
            f"{chunk.camera_id}:person:{suffix}",
            [INSIDE_M] * 20,
            camera_id=chunk.camera_id,
            start_s=start_s,
        )
        self.store.append([track])
        return [track]


class GatedVerifier:
    """Verifies instantly, except the candidates of `blocked_cameras`, which wait for `gate`."""

    def __init__(self, blocked_cameras: Iterable[str] = (), *, raise_for: str | None = None):
        self.blocked = set(blocked_cameras)
        self.raise_for = raise_for
        self.gate = threading.Event()
        self.threads: set[str] = set()
        self.started: list[str] = []
        self.lock = threading.Lock()

    def verify(self, candidate: CandidateEvent, rule: RuleIR) -> VerifierVerdict:
        with self.lock:
            self.threads.add(threading.current_thread().name)
            self.started.append(candidate.camera_id)
        if candidate.camera_id == self.raise_for:
            msg = "verifier exploded"
            raise RuntimeError(msg)
        if candidate.camera_id in self.blocked:
            assert self.gate.wait(TIMEOUT_S)
        return make_verdict()


class SleepyVerifier:
    """Takes `seconds` of wall time per verdict, like a remote Tier 1 call."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls = 0

    def verify(self, candidate: CandidateEvent, rule: RuleIR) -> VerifierVerdict:
        self.calls += 1
        time.sleep(self.seconds)
        return make_verdict()


class ThreadRecordingClock(FakeClock):
    """A frozen clock that remembers which threads read it."""

    def __init__(self) -> None:
        super().__init__()
        self.threads: set[str] = set()

    def now(self) -> datetime:
        self.threads.add(threading.current_thread().name)
        return super().now()


def two_camera_plan() -> ActivePlan:
    """A person in the sink zone, on cam1 and (its own zone) on cam2."""
    rules = tuple(
        make_rule({"op": "inside", "object": "person", "zone": zone}, rule_id=f"in-{zone}")
        for zone in ("sink_cam1", "sink_cam2")
    )
    zones = {
        f"sink_{camera}": SINK_ZONE.model_copy(update={"camera_id": camera})
        for camera in ("cam1", "cam2")
    }
    return ActivePlan(
        rules=rules,
        zones=zones,
        calibration=CAL,
        rule_cameras={"in-sink_cam1": ("cam1",), "in-sink_cam2": ("cam2",)},
    )


def chain_records(loop: RuntimeLoop, tmp_path: Path) -> list[tuple[str, dict[str, object]]]:
    path = tmp_path / "chain" / "site" / f"{FakeClock().now().date().isoformat()}.jsonl"
    return [(r.kind, dict(r.payload)) for r in read_chain(path)] if path.is_file() else []


def verdicts(loop: RuntimeLoop, tmp_path: Path) -> int:
    return [kind for kind, _ in chain_records(loop, tmp_path)].count("verdict")


def event_id(kind: str, payload: dict[str, object]) -> str:
    if kind == "candidate":
        candidate = payload["candidate"]
        assert isinstance(candidate, dict)
        return str(candidate["event_id"])
    return str(payload["event_id"])


def assert_chain_order(records: list[tuple[str, dict[str, object]]]) -> None:
    """Each verdict and action follows its candidate; each camera's candidates in chunk order."""
    seen: set[str] = set()
    windows: dict[str, list[object]] = {}
    for kind, payload in records:
        if kind == "plan":
            continue
        eid = event_id(kind, payload)
        if kind == "candidate":
            assert eid not in seen
            seen.add(eid)
            context = DecisionContext.model_validate(payload["context"])
            candidate = payload["candidate"]
            assert isinstance(candidate, dict)
            windows.setdefault(str(candidate["camera_id"]), []).append(context.t1)
        else:
            assert eid in seen, f"{kind} for {eid} before its candidate"
    for t1s in windows.values():
        assert t1s == sorted(t1s)  # type: ignore[type-var]


# --- verification never blocks the in-order path ----------------------------------------------


async def test_a_slow_verdict_does_not_block_later_chunks_or_other_cameras(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    tracker = CameraTracker(store)
    verifier = GatedVerifier(blocked_cameras={"cam1"})
    loop, bus, _, _ = make_loop(
        tmp_path, FakeClock(), verifier, tracker=tracker, store=store, pipeline_depth=4
    )
    loop.set_plan(two_camera_plan())
    async with running(loop, bus):
        for i in range(4):
            bus.publish(Topic.CHUNK_READY, make_chunk("cam1", i))
            bus.publish(Topic.CHUNK_READY, make_chunk("cam2", i))
        # cam1's verdict is stuck, yet every chunk of both cameras is consumed and cam2's
        # candidate is verified and chained.
        await wait_until(lambda: loop.metrics.chunks_processed == 8)
        await wait_until(lambda: verdicts(loop, tmp_path) == 1)
        await wait_until(lambda: loop.metrics.verify_pending == 1)
        kinds = [kind for kind, _ in chain_records(loop, tmp_path)]
        assert kinds.count("candidate") == 2
        assert kinds.count("verdict") == 1
        verifier.gate.set()
        await wait_until(lambda: loop.metrics.verify_pending == 0)
    records = chain_records(loop, tmp_path)
    assert [kind for kind, _ in records].count("verdict") == 2
    assert_chain_order(records)
    assert loop.metrics.stages["verify"].n == 2
    assert loop.metrics.verifications_failed == 0


async def test_verdicts_finishing_out_of_order_keep_the_chain_rules(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    tracker = CameraTracker(store, fresh=True)  # a new candidate every chunk
    verifier = GatedVerifier(blocked_cameras={"cam1"})
    loop, bus, _, actions = make_loop(
        tmp_path, FakeClock(), verifier, tracker=tracker, store=store, pipeline_depth=4
    )
    loop.set_plan(two_camera_plan())
    async with running(loop, bus):
        for i in range(3):
            bus.publish(Topic.CHUNK_READY, make_chunk("cam1", i))
            bus.publish(Topic.CHUNK_READY, make_chunk("cam2", i))
        await wait_until(lambda: loop.metrics.chunks_processed == 6)
        # cam2's three verdicts are chained while cam1's three wait.
        await wait_until(lambda: verdicts(loop, tmp_path) == 3)
        await wait_until(lambda: loop.metrics.verify_pending == 3)
        verifier.gate.set()
        await wait_until(lambda: loop.metrics.verify_pending == 0)
    records = chain_records(loop, tmp_path)
    kinds = [kind for kind, _ in records]
    assert kinds.count("candidate") == 6
    assert kinds.count("verdict") == 6
    assert kinds.count("action") == 6  # FakeActions notifies on every violation
    assert len(actions.runs) == 6
    assert_chain_order(records)
    # cam2's verdicts were chained while cam1's waited: verification is not in chunk order.
    verdict_cameras = [eid for kind, p in records if kind == "verdict" for eid in [p["event_id"]]]
    candidates = {event_id(k, p): p["candidate"] for k, p in records if k == "candidate"}
    cams = [candidates[str(eid)]["camera_id"] for eid in verdict_cameras]  # type: ignore[index]
    assert cams[:3] == ["cam2", "cam2", "cam2"]


async def test_act_first_runs_in_order_before_verification(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    tracker = CameraTracker(store)
    verifier = GatedVerifier(blocked_cameras={"cam1"})
    strips = FakeStrips()
    loop, bus, _, actions = make_loop(
        tmp_path,
        FakeClock(),
        verifier,
        tracker=tracker,
        store=store,
        strips=strips,
        pipeline_depth=4,
    )
    loop.set_plan(sink_plan(severity="high"))
    async with running(loop, bus):
        bus.publish(Topic.CHUNK_READY, make_chunk("cam1", 0))
        await wait_until(lambda: loop.metrics.verify_pending == 1)
        # The safety action is chained while Tier 1 is still out.
        kinds = [kind for kind, _ in chain_records(loop, tmp_path)]
        assert kinds == ["plan", "candidate", "action"]
        assert [d.act_first for d in actions.runs] == [True]
        verifier.gate.set()
        await wait_until(lambda: loop.metrics.verify_pending == 0)
    kinds = [kind for kind, _ in chain_records(loop, tmp_path)]
    assert kinds == ["plan", "candidate", "action", "verdict", "verdict"]
    assert len(strips.modes) == 1  # the act-first strip is reused for verification


async def test_a_failing_verification_is_decided_and_the_loop_carries_on(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    tracker = CameraTracker(store)
    verifier = GatedVerifier(raise_for="cam1")
    loop, bus, _, _ = make_loop(
        tmp_path, FakeClock(), verifier, tracker=tracker, store=store, pipeline_depth=2
    )
    loop.set_plan(two_camera_plan())
    async with running(loop, bus):
        for camera in ("cam1", "cam2"):
            for i in range(2):
                bus.publish(Topic.CHUNK_READY, make_chunk(camera, i))
        await wait_until(
            lambda: loop.metrics.chunks_processed == 4 and loop.metrics.verify_pending == 0
        )
    metrics = loop.metrics
    assert (metrics.chunks_failed, metrics.verifications_failed, metrics.verify_errors) == (0, 1, 0)
    records = chain_records(loop, tmp_path)
    assert [kind for kind, _ in records].count("candidate") == 2
    # cam2's verdict, and for cam1 (whose verifier raised) an error verdict and its decision.
    reasons = [str(p.get("reason", "")) for kind, p in records if kind == "verdict"]
    assert (
        sum(r.startswith("verifier_error: RuntimeError: verifier exploded") for r in reasons) == 1
    )
    decisions = [p["decision"] for kind, p in records if kind == "verdict" and "decision" in p]
    assert [d["route"] for d in decisions] == ["human_queue"]  # type: ignore[index]
    assert_chain_order(records)


# --- executors ---------------------------------------------------------------------------------


async def test_each_stage_runs_on_its_own_executor(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    tracker = CameraTracker(store)
    verifier = GatedVerifier()
    verifier.gate.set()
    clock = ThreadRecordingClock()
    loop, bus, _, _ = make_loop(
        tmp_path, clock, verifier, tracker=tracker, store=store, pipeline_depth=2
    )
    loop.set_plan(sink_plan())
    async with running(loop, bus):
        for i in range(3):
            bus.publish(Topic.CHUNK_READY, make_chunk("cam1", i))
        await wait_until(lambda: loop.metrics.chunks_processed == 3)
        await wait_until(lambda: verdicts(loop, tmp_path) == 1)

    def prefixes(names: set[str]) -> set[str]:
        return {name.rsplit("_", 1)[0] for name in names}

    assert prefixes(tracker.threads["segment"]) == {"rulecam-segment"}
    assert prefixes(tracker.threads["stitch"]) == {"rulecam-ordered"}
    assert prefixes(verifier.threads) == {"rulecam-verify"}
    assert "rulecam-admit" in prefixes(clock.threads)  # the lag check reads the clock
    # Nothing ran on asyncio's shared default executor.
    every = clock.threads | tracker.threads["segment"] | tracker.threads["stitch"]
    assert not {name for name in every | verifier.threads if name.startswith("asyncio")}


async def test_a_saturated_default_executor_does_not_stall_the_loop(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    tracker = CameraTracker(store)
    loop, bus, _, _ = make_loop(tmp_path, FakeClock(), tracker=tracker, store=store)
    loop.set_plan(sink_plan())
    release = threading.Event()
    running_loop = asyncio.get_running_loop()
    hogs = [running_loop.run_in_executor(None, release.wait, TIMEOUT_S) for _ in range(64)]
    try:
        async with running(loop, bus):
            for i in range(3):
                bus.publish(Topic.CHUNK_READY, make_chunk("cam1", i))
            await wait_until(
                lambda: loop.metrics.chunks_processed == 3 and loop.metrics.verify_pending == 0
            )
    finally:
        release.set()
        await asyncio.gather(*hogs)
    assert tracker.stitched == [make_chunk("cam1", i).chunk_id for i in range(3)]


# --- the target: end to end within 1.5x of the SAM latency ------------------------------------


async def test_end_to_end_stays_near_sam_latency_with_slow_verification(tmp_path: Path) -> None:
    clock = ScaledClock()
    store = InMemoryTrackStore()
    tracker = CameraTracker(store, fresh=True, sleep_s=SAM_LATENCY_S / SCALE)
    verifier = SleepyVerifier(VERIFY_S / SCALE)
    loop, bus, _, _ = make_loop(
        tmp_path,
        clock,  # type: ignore[arg-type]
        verifier,
        tracker=tracker,
        store=store,
        pipeline_depth=4,
    )
    loop.set_plan(sink_plan())
    n = 10
    async with running(loop, bus):
        await publish_live(bus, clock, n)
        await wait_until(lambda: loop.metrics.stages["end_to_end"].n == n)
        metrics = loop.metrics
        await wait_until(lambda: loop.metrics.verify_pending == 0)
    stages = metrics.stages
    # Every chunk raised a candidate whose verdict took 10 s, yet chunks went through as fast
    # as SAM allowed: end to end within 1.5x of the concept pass, and the loop never lagged.
    assert stages["segment"].n == n
    assert stages["end_to_end"].n == n
    assert stages["segment"].p50_s >= SAM_LATENCY_S
    assert stages["end_to_end"].p50_s <= 1.5 * stages["segment"].p50_s
    assert not metrics.keyframe_mode
    assert metrics.chunks_skipped == 0
    assert verifier.calls == n
    assert loop.metrics.stages["verify"].p50_s >= VERIFY_S * 0.9


def test_handle_chunk_verifies_inline_and_times_every_stage(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    loop, _, _, _ = make_loop(
        tmp_path, FakeClock(), tracker=CameraTracker(store), store=store, strips=FakeStrips()
    )
    loop.set_plan(sink_plan())
    found = loop.handle_chunk(make_chunk("cam1", 0))
    assert len(found) == 1
    assert found[0].strip_paths == ()  # the candidate as found and chained, before its strip
    kinds = [kind for kind, _ in chain_records(loop, tmp_path)]
    assert kinds == ["plan", "candidate", "verdict", "action"]
    stages = loop.metrics.stages
    for stage in ("segment", "stitch", "rules", "candidates", "chain", "strip", "verify"):
        assert stages[stage].n >= 1, stage
    assert stages["queued"].n == 0  # only `run()` queues chunks
    assert loop.timings is not None


async def test_the_bus_publishes_from_worker_threads_safely(tmp_path: Path) -> None:
    store = InMemoryTrackStore()
    bus_loop, bus, _, _ = make_loop(
        tmp_path, FakeClock(), tracker=CameraTracker(store), store=store, pipeline_depth=2
    )
    bus_loop.set_plan(sink_plan())
    async with running(bus_loop, bus):
        bus.publish(Topic.CHUNK_READY, make_chunk("cam1", 0))
        await wait_until(lambda: Topic.VERDICT_NEW in bus.topics())
    assert isinstance(bus, RecordingBus)
    assert bus.topics().index(Topic.CANDIDATE_NEW) < bus.topics().index(Topic.VERDICT_NEW)
