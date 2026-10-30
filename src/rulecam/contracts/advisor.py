"""Site Advisor contracts (task book §4.5, SA §4)."""

from __future__ import annotations

from typing import Literal

from rulecam.contracts.core import Frozen, Provenance


class SlotClaim(Frozen):
    """A claim whose text has slots, never digits; code fills the slots."""

    template: str  # text with {slots}, never digits
    slots: dict[str, str]  # slot -> metric_result id
    grade: Literal["A", "B", "C"]


class Explanation(Frozen):
    """A candidate cause and the status of its data test."""

    id: str
    statement: str
    status: Literal["supported", "partly", "ruled_out", "untestable"]
    share_of_gap: float | None = None
    evidence_query_ids: tuple[str, ...] = ()


class Change(Frozen):
    """A recommended change from the closed vocabulary."""

    op: Literal[
        "reschedule_arrivals",
        "add_capacity",
        "move_location",
        "resequence",
        "queue_topology",
        "buffer_capacity",
    ]
    params: dict[str, str]


class Gain(Frozen):
    """Expected gain as an interval, with the queueing model used and its assumption checks."""

    metric: str
    interval: tuple[float, float]
    level: float
    unit: str
    model_rung: Literal["little", "erlang_c", "allen_cunneen", "simulation"]
    assumptions: dict[str, Literal["pass", "fail", "n/a"]]


class SafetyCheck(Frozen):
    """Result of checking a recommendation against active safety rules."""

    rules_checked: tuple[str, ...]
    verdict: Literal["pass", "pass_with_mitigation", "veto"]
    considered_not_recommended: tuple[tuple[str, str], ...] = ()  # (change, reason)


class Recommendation(Frozen):
    """A checked advice card: saw, why, do, gain, check, safety."""

    id: str
    status: Literal["proposed", "accepted", "measuring", "adopted", "reverted", "rejected"]
    scope_kind: Literal["station", "equipment", "zone", "flow"]  # never "person"
    scope_ids: tuple[str, ...]
    klass: Literal["do", "test", "hold"]
    provenance: Provenance
    saw: tuple[SlotClaim, ...]
    why: tuple[Explanation, ...]
    do: tuple[Change, ...]
    who_role: str
    revert: str
    expect: Gain
    safety: SafetyCheck
    rank_score: float
    measurement_plan_id: str | None = None
    numbers_total: int
    numbers_reproduced: int


class MeasurementPlan(Frozen):
    """A pre-registered test of whether an accepted recommendation worked."""

    id: str
    recommendation_id: str
    primary_metric: str
    guard_metrics: tuple[str, ...]
    design: Literal["switchback", "its", "did"]
    block_minutes: int
    n_pairs: int
    min_detectable_effect: float
    smallest_possible_p: float
    decision_rule: str
    preregistration_hash: str


class Verdict(Frozen):
    """The read-out of a measurement plan."""

    plan_id: str
    label: Literal["worked", "no_effect", "worse", "not_enough_data"]
    effect: float
    ci: tuple[float, float]
    p_value: float
    pairs_done: int
