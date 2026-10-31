"""Runtime integration tests with all fakes (P3-08)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rulecam.bus import Topic
from rulecam.evidence.chain import read_chain, verify_chain
from rulecam.runtime.loop import ActionDone, VerdictNew, chunk_end, plan_concepts
from tests.fakes import FakeClock, FakeVerifier, make_chunk, make_verdict
from tests.runtime.fakes import make_loop, sink_plan


def at_end_of(clock: FakeClock, index: int) -> FakeClock:
    clock.advance((chunk_end(make_chunk(index=index)) - clock.now()).total_seconds())
    return clock


def kinds(chain_path: Path) -> list[str]:
    return [r.kind for r in read_chain(chain_path)]


def test_medium_rule_verifies_then_acts_in_order(tmp_path: Path) -> None:
    clock = at_end_of(FakeClock(), 0)
    loop, bus, chain, actions = make_loop(tmp_path, clock)
    loop.set_plan(sink_plan())
    candidates = loop.handle_chunk(make_chunk(index=0))

    assert len(candidates) == 1
    path = chain.path_for(clock.now().date())
    assert kinds(path) == ["plan", "candidate", "verdict", "action"]
    assert verify_chain(path).ok
    assert bus.topics() == [Topic.CANDIDATE_NEW, Topic.VERDICT_NEW, Topic.ACTION_DONE]
    verdict = bus.published[1][1]
    assert isinstance(verdict, VerdictNew)
    assert verdict.event_id == candidates[0].event_id
    action = bus.published[2][1]
    assert isinstance(action, ActionDone)
    assert action.action.kind == "notify"
    assert [d.act_first for d in actions.runs] == [False]


def test_high_rule_acts_before_verification(tmp_path: Path) -> None:
    clock = at_end_of(FakeClock(), 0)
    verifier = FakeVerifier(make_verdict(verdict="no_violation", score=0.1))
    loop, bus, chain, actions = make_loop(tmp_path, clock, verifier)
    loop.set_plan(sink_plan("high"))
    loop.handle_chunk(make_chunk(index=0))

    path = chain.path_for(clock.now().date())
    assert kinds(path) == ["plan", "candidate", "action", "verdict", "verdict"]
    decision = read_chain(path)[-1].payload["decision"]
    assert isinstance(decision, dict)
    assert decision["act_first"] is True
    assert bus.topics() == [Topic.CANDIDATE_NEW, Topic.ACTION_DONE, Topic.VERDICT_NEW]
    assert len(actions.runs) == 1  # the fake policy routes auto, so nothing runs twice
    assert verifier.calls[0][0].strip_paths == ("strip/f0.png",)


def test_rejected_candidate_runs_no_action(tmp_path: Path) -> None:
    clock = at_end_of(FakeClock(), 0)
    loop, _, chain, _ = make_loop(
        tmp_path, clock, FakeVerifier(make_verdict(verdict="no_violation"))
    )
    loop.set_plan(sink_plan())
    loop.handle_chunk(make_chunk(index=0))
    assert kinds(chain.path_for(clock.now().date())) == ["plan", "candidate", "verdict"]


def test_overlapping_windows_do_not_repeat_an_episode(tmp_path: Path) -> None:
    clock = at_end_of(FakeClock(), 0)
    loop, _, _, _ = make_loop(tmp_path, clock)
    loop.set_plan(sink_plan())
    assert len(loop.handle_chunk(make_chunk(index=0))) == 1
    at_end_of(clock, 1)
    assert loop.handle_chunk(make_chunk(index=1)) == []
    assert loop.metrics.candidates == 1


def test_concepts_come_from_the_plan(tmp_path: Path) -> None:
    assert plan_concepts(sink_plan()) == ["person"]


def test_without_a_plan_nothing_happens(tmp_path: Path) -> None:
    clock = FakeClock()
    loop, bus, _, _ = make_loop(tmp_path, clock)
    assert loop.plan is None
    assert loop.handle_chunk(make_chunk()) == []
    assert bus.published == []


def test_backpressure_switches_to_keyframes_and_back(tmp_path: Path) -> None:
    clock = FakeClock()
    loop, _, _, _ = make_loop(tmp_path, clock, max_lag_s=6.0, keyframe_stride=3)
    loop.set_plan(sink_plan())
    clock.advance(60)  # every chunk below is ~57 s late
    for i in range(6):
        loop.handle_chunk(make_chunk(index=i))
    metrics = loop.metrics
    assert metrics.keyframe_mode
    assert metrics.chunks_processed == 2
    assert metrics.chunks_skipped == 4
    assert metrics.lag_s > 6

    at_end_of(clock, 20)
    loop.handle_chunk(make_chunk(index=20))
    assert not loop.metrics.keyframe_mode
    assert loop.metrics.chunks_processed == 3


async def test_run_consumes_chunk_ready(tmp_path: Path) -> None:
    clock = at_end_of(FakeClock(), 0)
    loop, bus, chain, _ = make_loop(tmp_path, clock)
    bus.bind_loop(asyncio.get_running_loop())
    loop.set_plan(sink_plan())
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    bus.publish(Topic.CHUNK_READY, make_chunk(index=0))
    for _ in range(200):
        if loop.metrics.chunks_processed:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert loop.metrics.chunks_processed == 1
    assert "verdict" in kinds(chain.path_for(clock.now().date()))
