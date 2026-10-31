"""Fakes for the Site Advisor pipeline: P4-08 statistics, Spark Open's devil, a recording bus."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rulecam.advisor.pipeline import RecordedWindow, SiteAdvisorPipeline
from rulecam.bus import Bus, Topic
from rulecam.contracts.advisor import Change, Gain
from rulecam.contracts.core import Provenance, Severity
from rulecam.contracts.rule_ir import (
    ActionSpec,
    ConceptSpec,
    DistanceM,
    Persist,
    RuleIR,
    RuleSource,
    ZoneSpec,
)
from tests.fakes import EPOCH, FakeClock, FakeLLM

T0 = EPOCH
T1 = EPOCH + timedelta(hours=4)
CATALOGUE = frozenset(
    {
        "turnaround",
        "wait_for_forklift",
        "handling",
        "forklift_active_share",
        "aisle_blocked_min",
    }
)


@dataclass(frozen=True)
class Result:
    """A P4-08-shaped metric result."""

    id: str
    metric: str
    value: float
    lo: float
    hi: float
    unit: str
    station_id: str = "dock_3"
    n: int = 41
    n_tracks: int = 30
    n_shifts: int = 4
    provenance: Provenance = Provenance.RECORDED
    group_by: tuple[str, ...] = ("station",)


@dataclass(frozen=True)
class WhatIf:
    """A P4-08-shaped what-if."""

    gain: Gain
    p20_weekly_value: float


GAIN = Gain(
    metric="wait_for_forklift",
    interval=(-14.0, -4.0),
    level=0.8,
    unit="min/truck",
    model_rung="erlang_c",
    assumptions={"poisson_arrivals": "pass", "stable_service": "pass"},
)
WAIT = Result("mr_wait", "wait_for_forklift", 22.0, 18.0, 26.0, "min")


def dock_results() -> dict[str, Result]:
    """Return dock-3 hypothesis results: one per status."""
    return {
        "forklift_active_share": Result(
            "mr_share", "forklift_active_share", 0.9, 0.85, 0.95, "share"
        ),
        "aisle_blocked_min": Result("mr_aisle", "aisle_blocked_min", 2.0, 1.0, 3.0, "min"),
        "handling": Result("mr_handling", "handling", 19.0, 15.0, 24.0, "min"),
    }


@dataclass
class FakeStats:
    """Serves preset metric results, shares and what-ifs, and records what was asked."""

    flagged: Sequence[Result] = (WAIT,)
    results: Mapping[str, Result] = field(default_factory=dock_results)
    shares: Mapping[str, float] = field(default_factory=lambda: {"forklift_active_share": 0.6})
    what_ifs: Mapping[str, WhatIf | None] = field(
        default_factory=lambda: {
            "resequence": WhatIf(GAIN, 60.0),
            "reschedule_arrivals": WhatIf(GAIN, 45.0),
            "add_capacity": WhatIf(GAIN, 200.0),
        }
    )
    asked_what_if: list[Change] = field(default_factory=list)

    def catalogue(self) -> frozenset[str]:
        return CATALOGUE

    def metric(self, metric_id: str, station_id: str, t0: datetime, t1: datetime) -> Result | None:
        return self.results.get(metric_id)

    def detect_bottlenecks(
        self, station_ids: Sequence[str], t0: datetime, t1: datetime
    ) -> Sequence[Result]:
        return self.flagged

    def gap_share(
        self, metric_id: str, station_id: str, t0: datetime, t1: datetime
    ) -> float | None:
        return self.shares.get(metric_id)

    def what_if(self, change: Change, station_id: str, t0: datetime, t1: datetime) -> WhatIf | None:
        self.asked_what_if.append(change)
        return self.what_ifs.get(change.op)


@dataclass
class FakeDevil:
    """Returns preset devil's-advocate proposals, or raises."""

    proposals: list[str] = field(
        default_factory=lambda: [
            "It could just be random noise.",
            "Trucks arrived off appointment.",
            "A carrier's dispatch system was down.",
        ]
    )
    error: Exception | None = None
    contexts: list[Mapping[str, object]] = field(default_factory=list)

    def devil_advocate(self, context: Mapping[str, object]) -> list[str]:
        self.contexts.append(context)
        if self.error is not None:
            raise self.error
        return list(self.proposals)


class RecordingBus(Bus):
    """A bus that records what was published."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[Topic, object]] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.published.append((topic, payload))
        super().publish(topic, payload)


def ped_forklift_rule(*, nested: bool = False, rule_id: str = "ped-forklift-2m") -> RuleIR:
    """Return the `ped-forklift-2m` rule on the docks (optionally with a nested predicate)."""
    distance = DistanceM(a="person.foot_point", b="forklift.foot_point", lt=2.0)
    return RuleIR(
        id=rule_id,
        source=RuleSource(kind="text", text="Keep 2 m between people and forklifts"),
        concepts={
            "person": ConceptSpec(prompt="person"),
            "forklift": ConceptSpec(prompt="forklift"),
        },
        zones={"dock_3": ZoneSpec(camera_id="cam3", kind="polygon")},
        predicate=Persist(of=distance, seconds=1.0) if nested else distance,
        severity=Severity.HIGH,
        actions=(ActionSpec(kind="log"),),
    )


def ask(**overrides: object) -> dict[str, object]:
    """Return a valid Ask answer for dock 3 over the recorded window."""
    answer: dict[str, object] = {
        "metric_ids": ["wait_for_forklift"],
        "scope_kind": "station",
        "scope_ids": ["dock_3"],
        "start": T0.isoformat(),
        "end": T1.isoformat(),
    }
    answer.update(overrides)
    return answer


def advice(*changes: dict[str, object]) -> dict[str, object]:
    """Return a Recommend answer."""
    return {"changes": list(changes)}


RESEQUENCE: dict[str, object] = {
    "tier": "exploit",
    "op": "resequence",
    "params": {"task": "replenishment", "to": "before the inbound wave"},
    "who_role": "dock_lead",
    "revert": "move replenishment back",
}
ADD_FORKLIFT: dict[str, object] = {
    "tier": "elevate",
    "op": "add_capacity",
    "params": {"resource": "forklift", "zone": "dock_3"},
    "who_role": "site_manager",
    "revert": "return the hired forklift",
}


def make_pipeline(
    spark: FakeLLM,
    *,
    stats: FakeStats | None = None,
    devil: FakeDevil | None = None,
    bus: Bus | None = None,
    rules: Sequence[RuleIR] = (),
    mitigations: Mapping[str, str] | None = None,
    windows: Sequence[RecordedWindow] = (RecordedWindow(T0, T1, "dock_3"),),
) -> SiteAdvisorPipeline:
    """Build a pipeline on fakes."""
    return SiteAdvisorPipeline(
        spark=spark,
        devil=devil or FakeDevil(),
        stats=stats or FakeStats(),
        bus=bus or Bus(),
        clock=FakeClock(),
        recorded_windows=windows,
        active_rules=rules,
        mitigations=mitigations,
    )
