"""Per-stage timings of the runtime loop (P3-08): samples, percentiles and chunk timelines."""

from __future__ import annotations

import threading

from rulecam.runtime.timings import STAGES, StageStats, StageTimings, percentile


def test_percentiles_per_stage_and_unseen_stages_are_zero() -> None:
    timings = StageTimings(samples=100)
    for i in range(1, 101):
        timings.record("sam", "cam1", f"c{i}", float(i))
    report = timings.report()
    assert list(report)[: len(STAGES)] == list(STAGES)
    assert report["sam"] == StageStats(n=100, p50_s=50.0, p95_s=95.0)
    assert report["verify"] == StageStats()


def test_only_the_last_samples_count_and_negative_durations_are_zero() -> None:
    timings = StageTimings(samples=2)
    for seconds in (100.0, -3.0, 4.0):
        timings.record("stitch", "cam1", "c1", seconds)
    assert timings.report()["stitch"] == StageStats(n=2, p50_s=0.0, p95_s=4.0)


def test_an_unknown_stage_is_reported_after_the_known_ones() -> None:
    timings = StageTimings()
    timings.record("zzz", "cam1", "c1", 1.0)
    assert list(timings.report())[-1] == "zzz"


def test_a_chunk_timeline_sums_its_stages_and_keeps_sam_per_concept() -> None:
    timings = StageTimings()
    timings.record("chain", "cam1", "c1", 0.25)
    timings.record("chain", "cam1", "c1", 0.5)
    timings.record_sam("cam1", "c1", "person", 4.0)
    timings.record_sam("cam1", "c1", "forklift", 6.0)
    timings.record("chain", "cam2", "c1", 9.0)  # another camera's chunk of the same id
    fields = timings.finish("cam1", "c1")
    assert fields == {
        "chain": 0.75,
        "sam": 10.0,
        "sam_by_concept": {"person": 4.0, "forklift": 6.0},
    }
    assert timings.finish("cam1", "c1") == {}  # closed
    assert timings.report()["chain"].n == 3  # every duration is a sample


def test_a_keyed_timeline_is_separate_from_its_chunk() -> None:
    timings = StageTimings()
    timings.record("rules", "cam1", "c1", 1.0)
    timings.record("verify", "cam1", "c1", 9.0, key="ev-1")
    assert timings.finish("cam1", "c1") == {"rules": 1.0}
    assert timings.finish("cam1", "c1", key="ev-1", event="runtime_verify_timing") == {
        "verify": 9.0,
        "key": "ev-1",
    }


def test_open_timelines_are_bounded() -> None:
    timings = StageTimings(max_open=2)
    for chunk_id in ("c1", "c2", "c3"):
        timings.record("rules", "cam1", chunk_id, 1.0)
    assert timings.finish("cam1", "c1") == {}  # the oldest was dropped
    assert timings.finish("cam1", "c3") == {"rules": 1.0}


def test_injected_monotonic_clock_and_thread_safety() -> None:
    timings = StageTimings(monotonic=lambda: 42.0)
    assert timings.now() == 42.0

    def work(index: int) -> None:
        for _ in range(200):
            timings.record("sam_queue", "cam1", f"c{index}", 0.001)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert timings.report()["sam_queue"].n == 200  # bounded by `samples`


def test_percentile_nearest_rank() -> None:
    assert percentile([], 50) == 0.0
    assert percentile([3.0, 1.0, 2.0], 50) == 2.0
    assert percentile([3.0, 1.0, 2.0], 100) == 3.0
