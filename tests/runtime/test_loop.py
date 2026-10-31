"""Runtime integration tests with all fakes (P3-08)."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from rulecam.bus import Topic
from rulecam.contracts import PrivacyMode
from rulecam.evidence.chain import read_chain, verify_chain
from rulecam.rules import OperatorExclusion
from rulecam.rules.backtest import read_candidate_payload
from rulecam.runtime.loop import ActionDone, ActivePlan, VerdictNew, chunk_end, plan_concepts
from tests.fakes import FakeClock, FakeVerifier, InMemoryTrackStore, make_chunk, make_verdict
from tests.runtime.fakes import (
    FailingTracker,
    FakeStrips,
    SteadyTracker,
    make_loop,
    persist_plan,
    sequence_plan,
    sink_plan,
)


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
    path = chain.path_for(clock.now().date())
    # `chunks_processed` counts at the start of handle_chunk, which runs in a worker thread, so
    # wait for the chain's verdict record itself rather than the counter.
    for _ in range(500):
        if path.is_file() and "verdict" in kinds(path):
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert loop.metrics.chunks_processed == 1
    assert "verdict" in kinds(path)


def test_strip_gets_the_plans_privacy_mode(tmp_path: Path) -> None:
    clock = at_end_of(FakeClock(), 0)
    strips = FakeStrips()
    loop, _, _, _ = make_loop(tmp_path, clock, strips=strips)
    loop.set_plan(sink_plan(privacy_mode=PrivacyMode.STRICT))
    loop.handle_chunk(make_chunk(index=0))
    assert strips.modes == [PrivacyMode.STRICT]


def test_active_plan_defaults_to_strict_privacy() -> None:
    assert ActivePlan(rules=()).privacy_mode is PrivacyMode.STRICT


def test_windows_over_a_skipped_chunk_are_blind_not_evaluated(tmp_path: Path) -> None:
    clock = FakeClock()
    store = InMemoryTrackStore()
    loop, bus, _, _ = make_loop(
        tmp_path, clock, max_lag_s=6.0, keyframe_stride=3, tracker=SteadyTracker(store), store=store
    )
    loop.set_plan(persist_plan(seconds=4.0))
    clock.advance(60)
    for i in range(3):  # processed, skipped, skipped
        loop.handle_chunk(make_chunk(index=i))
    assert loop.metrics.chunks_skipped == 2
    before = bus.topics().count(Topic.CANDIDATE_NEW)
    at_end_of(clock, 3)  # caught up; only 3 s observed since the gap, the rule needs 4 s
    assert loop.handle_chunk(make_chunk(index=3)) == []
    assert loop.metrics.windows_blind >= 1
    assert bus.topics().count(Topic.CANDIDATE_NEW) == before
    at_end_of(clock, 4)  # 6 s observed after the gap: enough for a 4 s persist
    assert len(loop.handle_chunk(make_chunk(index=4))) == 1


def test_an_instantaneous_rule_is_judged_on_the_window_after_a_gap(tmp_path: Path) -> None:
    clock = FakeClock()
    loop, _, _, _ = make_loop(tmp_path, clock, max_lag_s=6.0, keyframe_stride=3)
    loop.set_plan(sink_plan())
    clock.advance(60)
    for i in range(3):  # processed, skipped, skipped
        loop.handle_chunk(make_chunk(index=i))
    at_end_of(clock, 3)
    blind = loop.metrics.windows_blind
    assert len(loop.handle_chunk(make_chunk(index=3))) == 1  # not blind for a whole window
    assert loop.metrics.windows_blind == blind


def test_a_sequence_rule_needs_the_whole_window_after_a_gap(tmp_path: Path) -> None:
    clock = FakeClock()
    loop, _, _, _ = make_loop(tmp_path, clock, max_lag_s=6.0, keyframe_stride=3)
    loop.set_plan(sequence_plan())
    clock.advance(60)
    for i in range(2):  # processed, skipped
        loop.handle_chunk(make_chunk(index=i))
    at_end_of(clock, 9)  # 24 s observed after the gap, still inside the 30 s window
    blind = loop.metrics.windows_blind
    loop.handle_chunk(make_chunk(index=9))
    assert loop.metrics.windows_blind == blind + 1


def test_dedup_entries_older_than_the_window_are_forgotten(tmp_path: Path) -> None:
    clock = at_end_of(FakeClock(), 0)
    loop, _, _, _ = make_loop(tmp_path, clock)
    loop.set_plan(sink_plan())
    loop.handle_chunk(make_chunk(index=0))
    assert len(loop._state.last_end) == 1
    at_end_of(clock, 40)
    loop.handle_chunk(make_chunk(index=40))  # a new episode far later: old key pruned first
    assert all(end > chunk_end(make_chunk(index=30)) for end in loop._state.last_end.values())


async def test_run_survives_a_failing_chunk(tmp_path: Path) -> None:
    clock = at_end_of(FakeClock(), 1)
    loop, bus, _, _ = make_loop(tmp_path, clock, tracker=FailingTracker())
    bus.bind_loop(asyncio.get_running_loop())
    loop.set_plan(sink_plan())
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0)
    bus.publish(Topic.CHUNK_READY, make_chunk(index=0))
    bus.publish(Topic.CHUNK_READY, "not a chunk")
    bus.publish(Topic.CHUNK_READY, make_chunk(index=1))
    for _ in range(200):
        if loop.metrics.chunks_processed == 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert loop.metrics.chunks_failed == 1
    assert loop.metrics.chunks_processed == 2


def test_candidate_records_rederive_in_the_audit(tmp_path: Path) -> None:
    from rulecam.evidence.audit import audit_chain  # noqa: PLC0415

    clock = at_end_of(FakeClock(), 0)
    loop, _, chain, _ = make_loop(tmp_path, clock)
    loop.set_plan(sink_plan())
    loop.handle_chunk(make_chunk(index=0))
    record = next(
        r for r in read_chain(chain.path_for(clock.now().date())) if r.kind == "candidate"
    )
    assert set(record.payload) == {"candidate", "context"}
    store = loop._store  # the same store the loop evaluated on
    report = audit_chain(chain.path_for(clock.now().date()), store)
    assert report.summary().endswith("1/1 decisions reproduced")


def test_the_owner_operator_override_reaches_the_decision_context(tmp_path: Path) -> None:
    """WS1c: operator exclusion is on by default and off for the rules the owner lists."""
    plan = sink_plan()
    for disabled, expected in (((), True), ((plan.rules[0].id,), False)):
        clock = at_end_of(FakeClock(), 0)
        operators = OperatorExclusion(disabled_rules=disabled)
        loop, _, chain, _ = make_loop(tmp_path / str(expected), clock, operators=operators)
        loop.set_plan(plan)
        loop.handle_chunk(make_chunk(index=0))
        records = read_chain(chain.path_for(clock.now().date()))
        record = next(r for r in records if r.kind == "candidate")
        assert read_candidate_payload(record)[1].operator_exclusion is expected
