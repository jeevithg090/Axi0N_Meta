"""Rule health: measured precision and an SPRT that pauses a rule that has gone bad (P4-09).

A rule that fires wrongly costs more than a rule that does not fire: people stop trusting the
system and then stop reading any of it. So each rule's precision is tracked from the labels
humans actually gave, and a rule whose precision has dropped is **paused automatically**, with a
reason, rather than left running until someone notices.

**What counts as evidence.** Only an alert a human judged:

* a `label` record with `label: false_alarm` (the "False alarm" button, `act/api.py`) — a false
  positive;
* a `label` record with `label: confirmed` — a true positive. Nothing writes this yet; the
  vocabulary is here so the acknowledgement path can start writing it (see "Known gaps" in
  `README.md`);
* an **upheld** dispute (`worker/log.py`) — a reviewer agreed with the worker, so a false
  positive;
* a **rejected** dispute — a reviewer looked and kept the alert, so a true positive.

An alert nobody labelled is **not** evidence and is not counted. Counting unlabelled alerts as
correct is the obvious way to make precision look good, and it is the one thing this module must
never do, so `RulePrecision` carries `unlabelled` next to the estimate: a precision of 1.0 from
two labels and four hundred unlabelled alerts should not reassure anybody. A drill label
(`drill: true`) is a rehearsal, not a real alert, and is skipped.

**The interval** is Wilson 95% over the labelled alerts (`rulecam.stats.intervals`). It accounts
for how few labels there are and for nothing else: not for who labelled them, not for labellers
seeing each other's decisions, not for a label that is itself wrong.

**The test.** A Wald SPRT on precision, H0 `p = p0` (healthy) against H1 `p = p1` (unhealthy,
`p1 < p0`). The statistic is the log-likelihood ratio **in favour of H0**, so it moves down on
each false positive:

    llr += log(p0 / p1)             on a true positive
    llr += log((1 - p0) / (1 - p1)) on a false positive

with boundaries `log((1 - alpha) / beta)` above (healthy, stop) and `log(alpha / (1 - beta))`
below (unhealthy, **pause**). Between them the test keeps going and nothing happens: an SPRT's
whole point is that it does not decide early, so a rule with three labels is left alone. That is
also its cost — a rule that is quietly bad keeps running until enough labels arrive.

The SPRT assumes each labelled alert is an independent draw at a fixed precision. Neither holds
exactly: alerts cluster (one confusing scene makes several), and precision changes when the site
changes. So treat a pause as "look at this rule now", not as a measurement.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from rulecam.contracts.core import Frozen
from rulecam.contracts.events import Dispute, EventRecord
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.stats.intervals import wilson_interval

_log = get_logger(__name__)

CONFIDENCE: Final = 0.95
FALSE_ALARM: Final = "false_alarm"
CONFIRMED: Final = "confirmed"

Outcome = Literal["true_positive", "false_positive"]
Decision = Literal["healthy", "unhealthy", "continue"]
LabelSource = Literal["label", "dispute"]


class AlertLabel(Frozen):
    """One human judgement of one alert.

    Attributes:
        rule_id: The rule that fired.
        event_id: The alert judged.
        outcome: What the human said.
        source: Whether it came from a label record or a dispute review.
        ts: When the judgement was recorded, so labels can be read in order.
    """

    rule_id: str
    event_id: str
    outcome: Outcome
    source: LabelSource
    ts: datetime


class RulePrecision(Frozen):
    """One rule's measured precision over the alerts a human labelled.

    `unlabelled` is the number of that rule's alerts nobody judged. It is not part of the
    estimate; it is reported so nobody reads a precision off two labels as if it were settled.
    """

    rule_id: str
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    unlabelled: int = Field(default=0, ge=0)
    precision: float | None = None
    lo: float = 0.0
    hi: float = 1.0

    @property
    def labelled(self) -> int:
        """How many of this rule's alerts were judged."""
        return self.true_positives + self.false_positives

    @property
    def label_share(self) -> float | None:
        """Share of this rule's alerts that were judged, or `None` if it never fired."""
        total = self.labelled + self.unlabelled
        return None if total == 0 else self.labelled / total


class SprtConfig(Frozen):
    """The SPRT's hypotheses and error rates.

    Attributes:
        p0: Precision we are willing to run with.
        p1: Precision we want to catch; must be below `p0`.
        alpha: Chance of pausing a healthy rule.
        beta: Chance of leaving an unhealthy rule running.
    """

    p0: float = Field(default=0.80, gt=0, lt=1)
    p1: float = Field(default=0.50, gt=0, lt=1)
    alpha: float = Field(default=0.05, gt=0, lt=1)
    beta: float = Field(default=0.10, gt=0, lt=1)

    @model_validator(mode="after")
    def _p1_below_p0(self) -> Self:
        if self.p1 >= self.p0:
            msg = f"p1 ({self.p1}) must be below p0 ({self.p0}): the test looks for a drop"
            raise ValueError(msg)
        return self

    @property
    def upper(self) -> float:
        """Boundary above which the rule is called healthy and the test stops."""
        return math.log((1.0 - self.alpha) / self.beta)

    @property
    def lower(self) -> float:
        """Boundary below which the rule is called unhealthy and paused."""
        return math.log(self.alpha / (1.0 - self.beta))


class SprtResult(Frozen):
    """Where the SPRT stands after a sequence of labels."""

    llr: float
    upper: float
    lower: float
    decision: Decision
    n: int = Field(ge=0)
    crossed_at: int | None = None

    @property
    def should_pause(self) -> bool:
        """Whether this result calls for the rule to be paused."""
        return self.decision == "unhealthy"


class RuleHealth(Frozen):
    """One rule's precision, its SPRT and whether it is paused."""

    rule_id: str
    precision: RulePrecision
    sprt: SprtResult
    paused: bool = False
    reason: str = ""


def _step(outcome: Outcome, config: SprtConfig) -> float:
    """The log-likelihood increment in favour of H0 for one labelled alert."""
    if outcome == "true_positive":
        return math.log(config.p0 / config.p1)
    return math.log((1.0 - config.p0) / (1.0 - config.p1))


def sprt(outcomes: Sequence[Outcome], config: SprtConfig | None = None) -> SprtResult:
    """Run the Wald SPRT over a sequence of labelled outcomes, in order.

    The test stops at the first label that crosses a boundary; later labels are not read, which
    is what makes the error rates hold.

    Args:
        outcomes: Labelled outcomes, oldest first.
        config: Hypotheses and error rates; the defaults are p0 0.80 against p1 0.50.

    Returns:
        The statistic, the boundaries, the decision and the 1-based label it crossed at.
    """
    config = config or SprtConfig()
    llr = 0.0
    for i, outcome in enumerate(outcomes, start=1):
        llr += _step(outcome, config)
        if llr <= config.lower:
            return SprtResult(
                llr=llr,
                upper=config.upper,
                lower=config.lower,
                decision="unhealthy",
                n=i,
                crossed_at=i,
            )
        if llr >= config.upper:
            return SprtResult(
                llr=llr,
                upper=config.upper,
                lower=config.lower,
                decision="healthy",
                n=i,
                crossed_at=i,
            )
    return SprtResult(
        llr=llr,
        upper=config.upper,
        lower=config.lower,
        decision="continue",
        n=len(outcomes),
        crossed_at=None,
    )


def read_labels(records: Iterable[EventRecord]) -> tuple[AlertLabel, ...]:
    """Read every human judgement of an alert out of a run of chain records.

    Drill labels are rehearsals and are skipped. A record that cannot be read is logged and
    skipped rather than failing the whole read, because rule health must not be the reason the
    runtime stops.

    Args:
        records: Chain records, in chain order.

    Returns:
        The labels found, in the order they were recorded.
    """
    found: list[AlertLabel] = []
    for record in records:
        try:
            label = _label_of(record)
        except (KeyError, TypeError, ValueError) as exc:
            _log.warning("rule_health_record_skipped", seq=record.seq, error=str(exc))
            continue
        if label is not None:
            found.append(label)
    return tuple(found)


def _label_of(record: EventRecord) -> AlertLabel | None:
    payload = record.payload
    if record.kind == "label":
        if payload.get("drill") is True:
            return None
        verdict = payload.get("label")
        outcome = _outcome_of_label(verdict)
        if outcome is None or "rule_id" not in payload:
            return None
        return AlertLabel(
            rule_id=str(payload["rule_id"]),
            event_id=str(payload["event_id"]),
            outcome=outcome,
            source="label",
            ts=record.ts,
        )
    if record.kind == "dispute":
        dispute = Dispute.model_validate(payload["dispute"])
        if dispute.outcome == "pending":
            return None  # nobody has reviewed it, so it says nothing either way
        return AlertLabel(
            rule_id=str(payload["rule_id"]),
            event_id=str(payload["event_id"]),
            outcome="false_positive" if dispute.outcome == "upheld" else "true_positive",
            source="dispute",
            ts=record.ts,
        )
    return None


def _outcome_of_label(verdict: object) -> Outcome | None:
    if verdict == FALSE_ALARM:
        return "false_positive"
    if verdict == CONFIRMED:
        return "true_positive"
    return None


def precision_of(
    rule_id: str, labels: Sequence[AlertLabel], *, alerts: int = 0, confidence: float = CONFIDENCE
) -> RulePrecision:
    """Measure one rule's precision over its labels.

    A rule's alert is counted once even if it was judged twice (a false-alarm label and then an
    upheld dispute, say): the latest judgement of each alert wins, so one alert is one trial.

    Args:
        rule_id: The rule to measure.
        labels: Labels of any rule; only this rule's are used.
        alerts: How many alerts this rule raised in the same window, for `unlabelled`.
        confidence: Interval confidence.

    Returns:
        The counts, the estimate and its Wilson interval.
    """
    latest: dict[str, AlertLabel] = {}
    for label in sorted((x for x in labels if x.rule_id == rule_id), key=lambda x: x.ts):
        latest[label.event_id] = label
    tp = sum(x.outcome == "true_positive" for x in latest.values())
    fp = sum(x.outcome == "false_positive" for x in latest.values())
    lo, hi = wilson_interval(tp, tp + fp, confidence)
    return RulePrecision(
        rule_id=rule_id,
        true_positives=tp,
        false_positives=fp,
        unlabelled=max(alerts - len(latest), 0),
        precision=None if tp + fp == 0 else tp / (tp + fp),
        lo=lo,
        hi=hi,
    )


def assess(
    rule_id: str,
    labels: Sequence[AlertLabel],
    *,
    alerts: int = 0,
    config: SprtConfig | None = None,
) -> RuleHealth:
    """Measure a rule and decide whether it should be paused.

    Args:
        rule_id: The rule to assess.
        labels: Labels of any rule; only this rule's are used.
        alerts: How many alerts this rule raised, for the unlabelled count.
        config: SPRT hypotheses and error rates.

    Returns:
        The precision, the SPRT and the pause decision with its reason.
    """
    config = config or SprtConfig()
    mine = sorted((x for x in labels if x.rule_id == rule_id), key=lambda x: x.ts)
    seen: dict[str, AlertLabel] = {}
    for label in mine:
        seen[label.event_id] = label
    ordered = sorted(seen.values(), key=lambda x: x.ts)
    result = sprt([x.outcome for x in ordered], config)
    measured = precision_of(rule_id, labels, alerts=alerts)
    reason = ""
    if result.should_pause:
        shown = "n/a" if measured.precision is None else f"{measured.precision:.0%}"
        reason = (
            f"Paused automatically: of {measured.labelled} alerts a person judged, "
            f"{measured.false_positives} were wrong (precision {shown}, "
            f"95% interval {measured.lo:.0%}-{measured.hi:.0%}). A sequential test "
            f"(p0={config.p0:.2f} against p1={config.p1:.2f}, alpha={config.alpha}, "
            f"beta={config.beta}) crossed its lower boundary at judgement "
            f"{result.crossed_at}. The rule stops producing alerts until someone fixes it "
            "and resumes it."
        )
    return RuleHealth(
        rule_id=rule_id,
        precision=measured,
        sprt=result,
        paused=result.should_pause,
        reason=reason,
    )


class RuleHealthMonitor:
    """Tracks every rule's health and holds the set of paused rules.

    A rule is paused once: the first assessment that crosses the boundary publishes one
    `rule_change` record on the evidence chain, and later assessments do not publish again.
    Resuming is a person's decision (`resume`), never automatic, because whatever made the rule
    wrong is still there until someone changes it.

    The monitor is safe to call from several threads; the runtime reads `is_paused` per chunk.
    """

    def __init__(self, chain: EventChain | None = None, config: SprtConfig | None = None) -> None:
        """Build a monitor.

        Args:
            chain: Evidence chain for `rule_change` records. Without one nothing is published,
                which is what the unit tests use.
            config: SPRT hypotheses and error rates.
        """
        self._chain = chain
        self._config = config or SprtConfig()
        self._paused: dict[str, str] = {}
        self._health: dict[str, RuleHealth] = {}
        self._lock = threading.Lock()

    @property
    def config(self) -> SprtConfig:
        """The SPRT configuration in use."""
        return self._config

    def is_paused(self, rule_id: str) -> bool:
        """Whether this rule is paused and must not produce alerts."""
        with self._lock:
            return rule_id in self._paused

    def paused_rules(self) -> frozenset[str]:
        """The paused rule ids, for `ActivePlan.paused`."""
        with self._lock:
            return frozenset(self._paused)

    def reason(self, rule_id: str) -> str:
        """Why this rule was paused, or an empty string if it is running."""
        with self._lock:
            return self._paused.get(rule_id, "")

    def health(self, rule_id: str) -> RuleHealth | None:
        """The last assessment of this rule, if it has been assessed."""
        with self._lock:
            return self._health.get(rule_id)

    def assess(self, rule_id: str, labels: Sequence[AlertLabel], *, alerts: int = 0) -> RuleHealth:
        """Assess one rule, pausing it and publishing a `rule_change` if the test says so.

        Args:
            rule_id: The rule to assess.
            labels: Labels of any rule; only this rule's are used.
            alerts: How many alerts the rule raised, for the unlabelled count.

        Returns:
            The assessment. `paused` is true if the rule is paused, whether this call paused it
            or an earlier one did.
        """
        found = assess(rule_id, labels, alerts=alerts, config=self._config)
        with self._lock:
            already = rule_id in self._paused
            if found.paused and not already:
                self._paused[rule_id] = found.reason
            self._health[rule_id] = found.model_copy(update={"paused": found.paused or already})
            publish = found.paused and not already
            stored = self._health[rule_id]
        if publish:
            self._publish(stored)
        return stored

    def assess_all(
        self,
        labels: Sequence[AlertLabel],
        *,
        alerts: Mapping[str, int] | None = None,
        rule_ids: Iterable[str] | None = None,
    ) -> tuple[RuleHealth, ...]:
        """Assess every rule that has a label (or every rule in `rule_ids`).

        Args:
            labels: Labels of any rule.
            alerts: Alerts raised per rule, for the unlabelled counts.
            rule_ids: Rules to assess; the default is every rule that has a label.

        Returns:
            One assessment per rule, by rule id.
        """
        counts = dict(alerts or {})
        ids = sorted(set(rule_ids) if rule_ids is not None else {x.rule_id for x in labels})
        return tuple(self.assess(i, labels, alerts=counts.get(i, 0)) for i in ids)

    def resume(self, rule_id: str, *, role: str, note: str = "") -> bool:
        """Resume a paused rule, by a person's decision, and publish the change.

        Args:
            rule_id: The rule to resume.
            role: Who resumed it, for the record.
            note: What they changed or checked.

        Returns:
            True if the rule was paused and is now running; False if it was not paused.
        """
        with self._lock:
            if rule_id not in self._paused:
                return False
            del self._paused[rule_id]
            found = self._health.get(rule_id)
            if found is not None:
                self._health[rule_id] = found.model_copy(update={"paused": False, "reason": ""})
        if self._chain is not None:
            self._chain.append(
                "rule_change",
                {
                    "rule_id": rule_id,
                    "change": "resumed",
                    "by": role,
                    "note": note,
                    "automatic": False,
                },
            )
        _log.info("rule_resumed", rule_id=rule_id, role=role)
        return True

    def _publish(self, found: RuleHealth) -> None:
        if self._chain is not None:
            self._chain.append(
                "rule_change",
                {
                    "rule_id": found.rule_id,
                    "change": "paused",
                    "automatic": True,
                    "reason": found.reason,
                    "precision": found.precision.precision,
                    "precision_lo": found.precision.lo,
                    "precision_hi": found.precision.hi,
                    "labelled": found.precision.labelled,
                    "false_positives": found.precision.false_positives,
                    "unlabelled": found.precision.unlabelled,
                    "llr": found.sprt.llr,
                    "lower_boundary": found.sprt.lower,
                    "crossed_at": found.sprt.crossed_at,
                    "p0": self._config.p0,
                    "p1": self._config.p1,
                    "alpha": self._config.alpha,
                    "beta": self._config.beta,
                },
            )
        _log.warning(
            "rule_auto_paused",
            rule_id=found.rule_id,
            precision=found.precision.precision,
            labelled=found.precision.labelled,
            crossed_at=found.sprt.crossed_at,
        )
