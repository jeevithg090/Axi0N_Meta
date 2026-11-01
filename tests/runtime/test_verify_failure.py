"""A failed Tier 1 verification is never silent, end to end (P3-05; real policy and actions).

Glimmer is a fake that raises (a stopped or unreachable GPU pod). Each candidate still gets an
explicit decision: an `unsure` `verifier_error` verdict and a human-queue decision on the chain,
a "Needs review: verifier unavailable — …" notification, and the metric.
"""

from __future__ import annotations

from pathlib import Path

from rulecam.clients.transport import ModelTransportError
from rulecam.contracts import CandidateEvent, RuleIR, VerifierVerdict
from rulecam.runtime.cost import CostMeter, CostSettings, CostWatch
from rulecam.runtime.idle_stop import IdleGpuStop
from rulecam.runtime.loop import RuntimeLoop
from rulecam.verify.guard import BreakerPolicy, CircuitBreaker, GuardedVerifier, describe
from tests.fakes import FakeClock, InMemoryTrackStore, make_chunk, make_verdict
from tests.runtime.fakes import FakeStrips, sink_plan
from tests.runtime.test_response import FakeTier2, Plugs, Rig, high_rule, loop_for
from tests.runtime.test_stall import CameraTracker

DOWN = ModelTransportError("POST /chat/completions failed: ConnectError")
NEEDS_REVIEW = f"Needs review: verifier unavailable — {describe(DOWN)}"


class DeadGlimmer:
    """Tier 1 with the GPU pod stopped: every call raises."""

    def __init__(self) -> None:
        self.calls = 0

    def verify(self, candidate: CandidateEvent, rule: RuleIR) -> VerifierVerdict:
        self.calls += 1
        raise DOWN


def guarded(inner: DeadGlimmer, **policy: float) -> GuardedVerifier:
    breaker = CircuitBreaker(BreakerPolicy(**policy))  # type: ignore[arg-type]
    return GuardedVerifier(inner, model_version="glimmer", breaker=breaker, sleep=lambda s: None)


def verdict_records(rig: Rig) -> list[dict[str, object]]:
    return [p for kind, p in rig.records() if kind == "verdict"]


def test_glimmer_raising_gives_a_human_queue_decision_chain_record_and_metric(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    inner = DeadGlimmer()
    response = rig.build(guarded(inner, retries=1), rulecam_actions_dry_run=False)
    loop = loop_for(rig, response, severity="medium")
    (candidate,) = loop.handle_chunk(make_chunk())
    assert inner.calls == 2  # retried once
    error, decision = verdict_records(rig)
    assert (error["event_id"], error["verdict"], error["score"]) == (
        candidate.event_id,
        "unsure",
        0.5,
    )
    assert error["reason"] == f"verifier_error: {describe(DOWN)}"
    assert decision["event_id"] == candidate.event_id
    assert decision["decision"] == {
        "alert": True,
        "route": "human_queue",
        "act_first": False,
        "reason": f"verifier unavailable — {describe(DOWN)}",
    }
    metrics = loop.metrics
    assert (metrics.verifications_failed, metrics.verify_errors) == (1, 0)
    assert metrics.verifier is not None
    assert metrics.verifier.failures == 1
    # The rule only logs, yet a person is told: the human queue always reaches someone.
    (alert,) = response.notifier.list()
    assert (alert.role, alert.title, alert.event_id) == (
        "site_lead",
        NEEDS_REVIEW,
        candidate.event_id,
    )
    actions = [p for kind, p in rig.records() if kind == "action"]
    assert [(a["kind"], a["gate"]) for a in actions] == [("notify", "human")]


def test_high_acts_first_then_a_failed_verify_goes_to_human_review(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    plugs = Plugs()
    response = rig.build(
        guarded(DeadGlimmer()),
        plugs=plugs,
        kasa_plug_host="10.0.0.5",
        rulecam_actions_dry_run=False,
    )
    loop_for(rig, response).handle_chunk(make_chunk())
    assert plugs.built["10.0.0.5"].events == ["connect", "off"]  # the cut did not wait
    kinds = [kind for kind, _ in rig.records()]
    assert kinds == ["plan", "candidate", "action", "action", "verdict", "verdict", "action"]
    error, decision = verdict_records(rig)
    assert str(error["reason"]).startswith("verifier_error: ")
    assert decision["decision"]["route"] == "human_queue"  # type: ignore[index]
    assert decision["decision"]["act_first"] is True  # type: ignore[index]
    titles = sorted(a.title for a in response.notifier.list("dock_lead"))
    assert titles == ["Alert: test", NEEDS_REVIEW]
    actions = [p for kind, p in rig.records() if kind == "action"]
    assert [a["kind"] for a in actions] == ["interlock_cut", "notify", "notify"]  # no second cut


def test_low_and_medium_with_a_failed_verify_are_not_suppressed(tmp_path: Path) -> None:
    for severity in ("low", "medium"):
        rig = Rig(tmp_path / severity)
        response = rig.build(guarded(DeadGlimmer()))  # dry run (the default)
        loop_for(rig, response, severity=severity).handle_chunk(make_chunk())
        _, decision = verdict_records(rig)
        assert decision["decision"]["alert"] is True  # type: ignore[index]
        assert decision["decision"]["route"] == "human_queue"  # type: ignore[index]
        actions = [p for kind, p in rig.records() if kind == "action"]
        assert [a["detail"] for a in actions] == ["dry-run: would notify site_lead (needs review)"]


def test_a_dead_glimmer_opens_the_breaker_and_later_candidates_fail_fast(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    inner = DeadGlimmer()
    response = rig.build(guarded(inner, retries=0, failure_threshold=2, cooldown_s=300.0))
    store = InMemoryTrackStore()
    loop = RuntimeLoop(
        bus=rig.bus,
        clock=rig.clock,
        tracker=CameraTracker(store, fresh=True),  # a new candidate every chunk
        store=store,
        verifier=response.tier1,
        policy=response.policy,
        actions=response.actions,
        strips=FakeStrips(),
        chain=rig.chain,
    )
    loop.set_plan(sink_plan("medium"))
    candidates = [c for i in range(4) for c in loop.handle_chunk(make_chunk(index=i))]
    assert len(candidates) == 4
    assert inner.calls == 2  # the last two never waited for a timeout
    metrics = loop.metrics
    assert metrics.verifications_failed == 4
    assert metrics.verifier_down is True
    assert metrics.verifier is not None
    assert (metrics.verifier.state, metrics.verifier.fast_failures) == ("open", 2)
    decisions = [p["decision"] for p in verdict_records(rig) if "decision" in p]
    assert [d["route"] for d in decisions] == ["human_queue"] * 4  # type: ignore[index]
    fast = [str(p["reason"]) for p in verdict_records(rig) if "reason" in p][-1]
    assert fast.startswith("verifier_error: circuit open, failing fast")


def test_a_failed_verify_escalates_to_tier2(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    tier2 = FakeTier2(make_verdict(tier="spark_open", score=0.95))
    response = rig.build(guarded(DeadGlimmer()), tier2=tier2, rulecam_actions_dry_run=False)
    base = high_rule().model_dump(mode="json")
    rule = RuleIR.model_validate(
        {
            **base,
            "severity": "medium",
            "verify": {**base["verify"], "tier2": "spark-open-on-disagree"},
        }
    )
    loop = loop_for(rig, response, severity="medium")
    loop.set_plan(sink_plan("medium").model_copy(update={"rules": (rule,)}))
    loop.handle_chunk(make_chunk())
    _, decision = verdict_records(rig)
    assert decision["decision"]["route"] == "auto"  # type: ignore[index]
    assert str(decision["decision"]["reason"]).startswith("Tier 2 confirmed")  # type: ignore[index]
    titles = [a.title for a in response.notifier.list("dock_lead")]
    assert titles == ["Alert: test"]
    assert loop.metrics.verifications_failed == 1


def test_the_api_ticks_the_tier1_health_probe(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    response = rig.build(guarded(DeadGlimmer()))
    assert len(response.tickers) == 2
    plain = Rig(tmp_path / "plain").build()
    assert len(plain.tickers) == 1  # a verifier without a breaker has nothing to probe


def test_the_idle_gpu_stop_waits_for_pending_verifications() -> None:
    class Stopper:
        def __init__(self) -> None:
            self.stopped: list[list[str]] = []

        def stop(self, pods: object) -> list[str]:
            self.stopped.append(list(pods))  # type: ignore[call-overload]
            return ["glimmer-id"]

    clock = FakeClock()
    stopper = Stopper()
    pending = [1]
    watch = CostWatch(
        CostMeter(CostSettings(), usage=dict),
        clock=clock,
        plan=lambda: None,  # no camera has an active plan
        last_seen=dict,
        idle_stop=IdleGpuStop(stopper, after_s=10.0),
        busy=lambda: pending[0] > 0,
    )
    for _ in range(3):
        watch.tick()
        clock.advance(10.0)
    assert stopper.stopped == []  # a candidate still waits for Tier 1
    pending[0] = 0
    for _ in range(3):
        watch.tick()
        clock.advance(10.0)
    assert stopper.stopped == [["rulecam-glimmer", "rulecam-sam"]]
