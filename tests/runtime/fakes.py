"""Fakes and builders for runtime-loop tests."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from rulecam.bus import Bus, Topic
from rulecam.contracts import (
    ActionRecord,
    CandidateEvent,
    Chunk,
    PrivacyMode,
    RuleIR,
    Track,
    VerifierVerdict,
)
from rulecam.evidence.chain import EventChain
from rulecam.replay.errors import ReplayMiss
from rulecam.runtime.loop import ActivePlan, ChunkTracker, RuntimeLoop
from rulecam.verify.decision import Decision
from tests.fakes import FakeClock, FakeVerifier, InMemoryTrackStore
from tests.rules.helpers import CAL, INSIDE_M, SINK_ZONE, make_rule, walk_track


class RecordingBus(Bus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[Topic, object]] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.published.append((topic, payload))
        super().publish(topic, payload)

    def topics(self) -> list[Topic]:
        return [t for t, _ in self.published]


class FakeTracker:
    """Appends a person standing in the sink zone for the chunk's first 2 s."""

    def __init__(self, store: InMemoryTrackStore) -> None:
        self.store = store
        self.calls: list[tuple[str, list[str]]] = []

    def process_chunk(self, chunk: Chunk, concepts: Iterable[str]) -> list[Track]:
        self.calls.append((chunk.chunk_id, list(concepts)))
        start_s = (chunk.start_ts - FakeClock().now()).total_seconds()
        track = walk_track("cam1:person:1", [INSIDE_M] * 20, start_s=start_s)
        self.store.append([track])
        return [track]


class FailingTracker:
    """Raises a `RulecamError` for the first chunk, then tracks like `FakeTracker`."""

    def __init__(self, store: InMemoryTrackStore | None = None) -> None:
        self.inner = FakeTracker(store or InMemoryTrackStore())
        self.failed = False

    def process_chunk(self, chunk: Chunk, concepts: Iterable[str]) -> list[Track]:
        if not self.failed:
            self.failed = True
            raise ReplayMiss(provider="meta", model="sam-3.1", key="k")
        return self.inner.process_chunk(chunk, concepts)


class FakePolicy:
    def act_first(self, candidate: CandidateEvent, rule: RuleIR) -> bool:
        return candidate.severity == "high"

    def decide(self, candidate: CandidateEvent, rule: RuleIR, tier1: VerifierVerdict) -> Decision:
        alert = tier1.verdict == "violation"
        return Decision(
            alert=alert, route="auto", act_first=self.act_first(candidate, rule), reason="fake"
        )


class FakeActions:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.runs: list[Decision] = []

    def run(
        self, candidate: CandidateEvent, rule: RuleIR, decision: Decision
    ) -> list[ActionRecord]:
        self.runs.append(decision)
        if not decision.alert:
            return []
        kind = "interlock_cut" if decision.act_first else "notify"
        return [ActionRecord(kind=kind, gate="auto", ts=self.clock.now(), ok=True)]


class FakeStrips:
    def __init__(self) -> None:
        self.modes: list[PrivacyMode] = []

    def render(self, candidate: CandidateEvent, chunk: Chunk, mode: PrivacyMode) -> CandidateEvent:
        self.modes.append(mode)
        return candidate.model_copy(update={"strip_paths": ("strip/f0.png",)})


def sink_plan(
    severity: str = "medium", privacy_mode: PrivacyMode = PrivacyMode.ANONYMOUS
) -> ActivePlan:
    rule = make_rule(
        {"op": "inside", "object": "person", "zone": "sink_zone"},
        rule_id="in-sink",
        severity=severity,
    )
    return ActivePlan(
        rules=(rule,),
        privacy_mode=privacy_mode,
        zones={"sink_zone": SINK_ZONE},
        calibration=CAL,
        plan_version_hash="pv1",
    )


def make_loop(
    tmp_path: Path,
    clock: FakeClock,
    verifier: FakeVerifier | None = None,
    *,
    strips: FakeStrips | None = None,
    tracker: ChunkTracker | None = None,
    **kwargs: float,
) -> tuple[RuntimeLoop, RecordingBus, EventChain, FakeActions]:
    bus = RecordingBus()
    store = InMemoryTrackStore()
    chain = EventChain(tmp_path / "chain", "site", clock=clock)
    actions = FakeActions(clock)
    loop = RuntimeLoop(
        bus=bus,
        clock=clock,
        tracker=tracker or FakeTracker(store),
        store=store,
        verifier=verifier or FakeVerifier(),
        policy=FakePolicy(),
        actions=actions,
        strips=strips or FakeStrips(),
        chain=chain,
        **kwargs,  # type: ignore[arg-type]
    )
    return loop, bus, chain, actions
