"""The runtime SAM concurrency cap and the runtime's own hosted segmenter (P3-08)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from rulecam.contracts import Chunk, Detection
from rulecam.perception.metrics import Backend
from rulecam.perception.router import LimitedSegmenter, Purpose, RoutingPolicy, SegmenterRouter
from rulecam.runtime.timings import StageTimings
from tests.fakes import FakeClock, FakeSegmenter, make_chunk

TIMEOUT_S = 10.0


class Ticker:
    """A monotonic clock that advances 1 s per reading."""

    def __init__(self) -> None:
        self.now = 0.0
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            self.now += 1.0
            return self.now


def test_the_cap_holds_calls_beyond_it_and_times_their_queue() -> None:
    lock = threading.Lock()
    running = 0
    peak = 0
    gate = threading.Event()

    def blocked(chunk: Chunk, concept: str) -> list[Detection]:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        assert gate.wait(TIMEOUT_S)
        with lock:
            running -= 1
        return []

    timings = StageTimings()
    limited = LimitedSegmenter(FakeSegmenter(responder=blocked), 2, timings=timings)
    with ThreadPoolExecutor(6) as pool:
        futures = [
            pool.submit(limited.segment, make_chunk(index=i), concept)
            for i in range(3)
            for concept in ("person", "forklift")
        ]
        threading.Timer(0.2, gate.set).start()
        for future in futures:
            assert future.result(TIMEOUT_S) == []
    assert peak == 2
    assert limited.max_concurrent == 2
    report = timings.report()
    assert report["sam_queue"].n == 6
    assert report["sam"].n == 6
    assert report["sam_queue"].p95_s > 0  # four calls waited for a slot
    by_concept = timings.finish("cam1", make_chunk(index=0).chunk_id)["sam_by_concept"]
    assert isinstance(by_concept, dict)
    assert set(by_concept) == {"person", "forklift"}


def test_a_failing_call_frees_its_slot_and_is_still_timed() -> None:
    def broken(chunk: Chunk, concept: str) -> list[Detection]:
        msg = "boom"
        raise RuntimeError(msg)

    timings = StageTimings()
    limited = LimitedSegmenter(
        FakeSegmenter(responder=broken), 1, timings=timings, monotonic=Ticker()
    )
    for _ in range(2):  # the second call would hang if the first had kept the slot
        with pytest.raises(RuntimeError):
            limited.segment(make_chunk(), "person")
    assert timings.report()["sam"] == timings.report()["sam"].model_copy(
        update={"n": 2, "p50_s": 1.0, "p95_s": 1.0}
    )


def test_without_timings_and_with_a_cap_below_one() -> None:
    inner = FakeSegmenter()
    limited = LimitedSegmenter(inner, 0)
    assert limited.max_concurrent == 1
    assert limited.segment(make_chunk(), "person", box_prompts={1: (0, 0, 1, 1)}) == []
    assert inner.calls[0].box_prompts == {1: (0, 0, 1, 1)}


@pytest.mark.parametrize("backend", [Backend.HOSTED, Backend.SELF_HOSTED])
def test_the_runtime_path_uses_its_own_hosted_segmenter(backend: Backend) -> None:
    hosted, runtime_hosted, self_hosted = FakeSegmenter(), FakeSegmenter(), FakeSegmenter()
    router = SegmenterRouter(
        hosted,
        self_hosted,
        clock=FakeClock(),
        policy=RoutingPolicy(runtime_backend=backend),
        runtime_hosted=runtime_hosted,
    )
    router.for_purpose(Purpose.PROBE).segment(make_chunk(), "person")
    router.for_purpose(Purpose.RUNTIME).segment(make_chunk(), "person")
    assert len(hosted.calls) == 1  # probes keep the full hosted client
    if backend is Backend.HOSTED:
        assert len(runtime_hosted.calls) == 1
        assert self_hosted.calls == []
    else:
        assert runtime_hosted.calls == []
        assert len(self_hosted.calls) == 1
