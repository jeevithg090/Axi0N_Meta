"""A paused rule stops producing candidates, through the real runtime loop (task P4-09).

The unit tests in `test_health.py` check the arithmetic. This one checks the thing that actually
matters: once rule health pauses a rule, the loop that would have raised an alert raises none,
and the evidence chain shows no candidate.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from rulecam.evidence.chain import read_chain
from rulecam.runtime.loop import chunk_end
from rulecam.verify.health import AlertLabel, RuleHealthMonitor
from tests.fakes import EPOCH, FakeClock, make_chunk
from tests.runtime.fakes import make_loop, sink_plan

RULE = "in-sink"


def at_end_of(clock: FakeClock, index: int) -> FakeClock:
    """Move `clock` to the end of chunk `index`, as `tests/runtime/test_loop.py` does."""
    clock.advance((chunk_end(make_chunk(index=index)) - clock.now()).total_seconds())
    return clock


def wrong_alerts(n: int, rule_id: str = RULE) -> list[AlertLabel]:
    """`n` alerts of one rule that a person judged wrong."""
    return [
        AlertLabel(
            rule_id=rule_id,
            event_id=f"ev-{i:03d}",
            outcome="false_positive",
            source="label",
            ts=EPOCH + timedelta(seconds=i),
        )
        for i in range(1, n + 1)
    ]


def kinds_in(path: Path) -> list[str]:
    return [r.kind for r in read_chain(path)]


def test_the_rule_alerts_while_it_is_healthy(tmp_path: Path) -> None:
    """The control: without a pause this plan and chunk produce one candidate."""
    clock = at_end_of(FakeClock(), 0)
    loop, _, chain, _ = make_loop(tmp_path, clock)
    loop.set_plan(sink_plan())
    assert len(loop.handle_chunk(make_chunk(index=0))) == 1
    assert "candidate" in kinds_in(chain.path_for(clock.now().date()))


def test_a_paused_rule_produces_no_candidate(tmp_path: Path) -> None:
    clock = at_end_of(FakeClock(), 0)
    loop, _, chain, _ = make_loop(tmp_path, clock)

    monitor = RuleHealthMonitor()
    assert monitor.assess(RULE, wrong_alerts(4)).paused

    loop.set_plan(sink_plan().with_paused(monitor.paused_rules()))
    assert loop.handle_chunk(make_chunk(index=0)) == []
    assert "candidate" not in kinds_in(chain.path_for(clock.now().date()))
    assert loop.metrics.candidates == 0


def test_resuming_the_rule_lets_it_alert_again(tmp_path: Path) -> None:
    clock = at_end_of(FakeClock(), 0)
    loop, _, _, _ = make_loop(tmp_path, clock)
    monitor = RuleHealthMonitor()
    monitor.assess(RULE, wrong_alerts(4))

    loop.set_plan(sink_plan().with_paused(monitor.paused_rules()))
    assert loop.handle_chunk(make_chunk(index=0)) == []

    assert monitor.resume(RULE, role="owner", note="zone redrawn")
    at_end_of(clock, 1)
    loop.set_plan(sink_plan().with_paused(monitor.paused_rules()))
    assert len(loop.handle_chunk(make_chunk(index=1))) == 1


def test_pausing_one_rule_leaves_the_others_running(tmp_path: Path) -> None:
    """Only the named rule stops; a pause is not a kill switch for the site."""
    clock = at_end_of(FakeClock(), 0)
    loop, _, _, _ = make_loop(tmp_path, clock)
    monitor = RuleHealthMonitor()
    monitor.assess("some_other_rule", wrong_alerts(4, "some_other_rule"))

    loop.set_plan(sink_plan().with_paused(monitor.paused_rules()))
    assert len(loop.handle_chunk(make_chunk(index=0))) == 1


def test_a_paused_rule_is_left_out_of_every_camera(tmp_path: Path) -> None:
    plan = sink_plan().with_paused(frozenset({RULE}))
    assert plan.rules_on("cam1") == ()
    assert plan.rules_on("any-other-camera") == ()
    assert len(plan.rules) == 1  # it stays in the plan: this is a pause, not an edit


def test_an_unpaused_plan_runs_every_rule(tmp_path: Path) -> None:
    plan = sink_plan()
    assert plan.paused == frozenset()
    assert len(plan.rules_on("cam1")) == 1
