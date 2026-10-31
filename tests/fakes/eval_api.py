"""Fakes and builders for the eval harness tests (no network, no ffmpeg, simulated time)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from rulecam.contracts.rule_ir import (
    ActionSpec,
    All,
    ConceptSpec,
    DistanceM,
    Moving,
    RuleIR,
    RuleSource,
)
from rulecam.eval.client import ChunkAck, Pairing, PlanRefusedError
from rulecam.eval.record import BusFrame, ChunkSlot, MetricsSample, RunRecord
from rulecam.eval.runner import Clock, LocalChunk
from tests.fakes import EPOCH

PLAN_HASH = "p" * 64


def forklift_rule(rule_id: str = "keep-clear") -> RuleIR:
    """A person within 2 m of a moving forklift."""
    return RuleIR(
        id=rule_id,
        source=RuleSource(kind="plan", plan_id="plan-1"),
        concepts={
            "person": ConceptSpec(prompt="person"),
            "forklift": ConceptSpec(prompt="forklift"),
        },
        predicate=All(
            of=(
                DistanceM(a="person", b="forklift", lt=2.0),
                Moving(object="forklift", speed_mps_gt=0.2),
            )
        ),
        severity="high",
        actions=(ActionSpec(kind="notify", role="dock_lead"),),
    )


def iso(ts: datetime) -> str:
    """Chain-style UTC timestamp."""
    return ts.isoformat().replace("+00:00", "Z")


def candidate_record(
    seq: int,
    event_id: str,
    start: datetime,
    end: datetime,
    *,
    written: datetime | None = None,
    camera_id: str = "phone-1",
    plan_hash: str = PLAN_HASH,
) -> dict[str, object]:
    """A `candidate` chain record as the runtime writes it."""
    return {
        "seq": seq,
        "kind": "candidate",
        "ts": iso(written or end),
        "site": "demo",
        "payload": {
            "candidate": {
                "event_id": event_id,
                "rule_id": "keep-clear",
                "camera_id": camera_id,
                "ts_start": iso(start),
                "ts_end": iso(end),
                "severity": "high",
                "predicate_trace": {"all": True, "subject": f"{camera_id}:forklift:1"},
                "strip_paths": [],
                "track_window_hash": "t" * 64,
                "plan_version_hash": plan_hash,
                "rule_ir_hash": "r" * 64,
            },
            "context": {},
        },
    }


def verdict_record(
    seq: int, event_id: str, verdict: str, at: datetime, *, tier: str = "glimmer"
) -> dict[str, object]:
    """A Tier 1 / Tier 2 `verdict` chain record."""
    return {
        "seq": seq,
        "kind": "verdict",
        "ts": iso(at),
        "site": "demo",
        "payload": {"event_id": event_id, "tier": tier, "verdict": verdict, "score": 0.5},
    }


def decision_record(seq: int, event_id: str, *, alert: bool, at: datetime) -> dict[str, object]:
    """An after-the-fact `decision` verdict record (act-first rules)."""
    return {
        "seq": seq,
        "kind": "verdict",
        "ts": iso(at),
        "site": "demo",
        "payload": {"event_id": event_id, "decision": {"alert": alert, "route": "auto"}},
    }


def slots(n: int, *, start: datetime = EPOCH, duration: float = 3.0) -> tuple[ChunkSlot, ...]:
    """`n` back-to-back chunk slots starting at `start` (clip second 0)."""
    return tuple(
        ChunkSlot(
            index=i,
            chunk_id=f"phone-1_{i:06d}",
            clip_start_s=i * duration,
            duration_s=duration,
            cam_start=start + timedelta(seconds=i * duration),
        )
        for i in range(n)
    )


def run_record(
    chain: list[dict[str, object]],
    *,
    clip: str = "yard",
    rule: str = "forklift_2m",
    n_chunks: int = 10,
    metrics: tuple[MetricsSample, ...] = (),
    bus: tuple[BusFrame, ...] = (),
    **extra: object,
) -> RunRecord:
    """A finished run on `phone-1` with plan `PLAN_HASH`."""
    return RunRecord.model_validate(
        {
            "clip": clip,
            "rule": rule,
            "camera_id": "phone-1",
            "plan_id": "plan-1",
            "plan_version_hash": PLAN_HASH,
            "activated": ("keep-clear",),
            "started_at": EPOCH - timedelta(minutes=2),
            "finished_at": EPOCH + timedelta(minutes=2),
            "chunks": slots(n_chunks),
            "chain": tuple(chain),
            "metrics": metrics,
            "bus": bus,
            **extra,
        }
    )


@dataclass
class SimClock:
    """Simulated time: `sleep` advances both the wall clock and the monotonic clock."""

    t: float = 0.0

    def now(self) -> datetime:
        return EPOCH + timedelta(seconds=self.t)

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)

    def clock(self) -> Clock:
        return Clock(now=self.now, monotonic=self.monotonic, sleep=self.sleep)


def fake_splitter(n: int, duration: float = 3.0) -> Callable[[Path, Path], list[LocalChunk]]:
    """A splitter that 'cuts' a clip into `n` chunks without touching the files."""

    def split(clip: Path, out_dir: Path) -> list[LocalChunk]:
        return [LocalChunk(i, out_dir / f"c{i:04d}.mp4", i * duration, duration) for i in range(n)]

    return split


@dataclass
class FakeApi:
    """An `ApiClient` with scripted answers, recording every upload with its simulated time."""

    clock: SimClock
    plan_ready_after_polls: int = 1
    refuse: str | None = None
    in_flight: list[int] = field(default_factory=lambda: [2, 1, 0])
    chain_for: Callable[[FakeApi], list[dict[str, object]]] = lambda api: []
    uploads: list[tuple[str, datetime]] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    polls: int = 0
    billed: int = 0

    def pair(self, label: str) -> Pairing:
        return Pairing(token="t" * 20, camera_id="phone-1", session="s")

    def upload(self, pairing: Pairing, chunk: Path) -> ChunkAck:
        self.uploads.append((chunk.name, self.clock.now()))
        self.billed += 50
        return ChunkAck(
            chunk_id=f"phone-1_{len(self.uploads) - 1:06d}", n_frames=30, width=640, height=360
        )

    def request_plan(self, text: str, lang: str) -> str:
        self.requests.append(text)
        return "plan-1"

    def plan(self, plan_id: str) -> dict[str, object] | None:
        self.polls += 1
        if self.polls < self.plan_ready_after_polls:
            return None
        return {"plan_id": plan_id, "rules": [forklift_rule().model_dump(mode="json")]}

    def approve(self, plan_id: str) -> dict[str, object]:
        if self.refuse is not None:
            raise PlanRefusedError(self.refuse)
        return {"activated": ["keep-clear"], "plan_version_hash": PLAN_HASH}

    def runtime_metrics(self) -> dict[str, object]:
        flight = self.in_flight.pop(0) if len(self.in_flight) > 1 else self.in_flight[0]
        return {
            "lag_s": 2.0,
            "keyframe_mode": False,
            "billed_frames": self.billed,
            "in_flight": flight,
        }

    def chain(self, day: date) -> list[dict[str, object]]:
        return self.chain_for(self) if day == self.clock.now().date() else []
