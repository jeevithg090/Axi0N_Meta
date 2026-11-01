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
* an **upheld** dispute (`worker/log.py`) — the reviewer agreed with the worker, so a false
  positive;
* a **rejected** dispute — the reviewer looked and kept the alert, so a true positive.

A dispute is reviewed by the Tier 2 model (Spark Open, `worker/disputes.py`), not by a person,
and an upheld dispute also writes a `label` with `source: worker_dispute`. Those judgements are
counted, but they are marked `decided_by="model"` and reported as `model_decided` so nobody
mistakes them for a person's call. A label with no rule is logged and skipped.

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

**Restarts.** One SPRT stops at its first boundary, so on its own an early "healthy" would hide
everything after it. So the test restarts from the next label each time it calls a rule healthy
(`sequential_sprt`), and it starts again from the moment a person resumes a rule. Each run keeps
Wald's error rates; over many runs the chance of some false pause grows, which is the price of
still looking.

**HIGH severity is never switched off.** A HIGH rule acts first (`Severity`), so pausing it on a
few "False alarm" taps would quietly remove a safety rule from every camera. When its test says
"pause", the rule is marked `needs_review` instead: it keeps running, one `rule_change` record
says why, and a person decides. LOW and MEDIUM rules are paused.

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

from rulecam.clock import SystemClock
from rulecam.contracts.core import Frozen, Severity
from rulecam.contracts.events import Dispute, EventRecord
from rulecam.contracts.protocols import Clock
from rulecam.evidence.chain import EventChain, utc_iso
from rulecam.log import get_logger
from rulecam.stats.intervals import wilson_interval

_log = get_logger(__name__)

CONFIDENCE: Final = 0.95
FALSE_ALARM: Final = "false_alarm"
CONFIRMED: Final = "confirmed"

Outcome = Literal["true_positive", "false_positive"]
Decision = Literal["healthy", "unhealthy", "continue"]
LabelSource = Literal["label", "dispute"]
DecidedBy = Literal["person", "model"]
HealthStatus = Literal["running", "paused", "needs_review"]
DISPUTE_LABEL_SOURCE: Final = "worker_dispute"


class AlertLabel(Frozen):
    """One human judgement of one alert.

    Attributes:
        rule_id: The rule that fired.
        event_id: The alert judged.
        outcome: What the human said.
        source: Whether it came from a label record or a dispute review.
        ts: When the judgement was recorded, so labels can be read in order.
        decided_by: `model` when the Tier 2 dispute reviewer made the call, not a person.
    """

    rule_id: str
    event_id: str
    outcome: Outcome
    source: LabelSource
    ts: datetime
    decided_by: DecidedBy = "person"


class RulePrecision(Frozen):
    """One rule's measured precision over the alerts a human labelled.

    `unlabelled` is the number of that rule's alerts nobody judged. It is not part of the
    estimate; it is reported so nobody reads a precision off two labels as if it were settled.
    `model_decided` is how many of the judged alerts the Tier 2 dispute reviewer decided.
    """

    rule_id: str
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    unlabelled: int = Field(default=0, ge=0)
    model_decided: int = Field(default=0, ge=0)
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
    """Where the SPRT stands after a sequence of labels.

    `crossed_at` is the 1-based label of the latest boundary crossing and `started_at` the
    number of labels that belonged to earlier, finished runs (`sequential_sprt`); both count
    from the start of the sequence given.
    """

    llr: float
    upper: float
    lower: float
    decision: Decision
    n: int = Field(ge=0)
    crossed_at: int | None = None
    started_at: int = Field(default=0, ge=0)

    @property
    def should_pause(self) -> bool:
        """Whether this result calls for the rule to be paused."""
        return self.decision == "unhealthy"


class RuleHealth(Frozen):
    """One rule's precision, its SPRT and what was done about it.

    Attributes:
        rule_id: The rule.
        precision: Measured precision over every label of the rule.
        sprt: The test over the labels since the rule was last resumed.
        severity: The rule's severity; HIGH is never paused.
        status: `running`, `paused` (LOW/MEDIUM) or `needs_review` (HIGH, still running).
        paused: Whether the rule is paused and must not be evaluated.
        reason: Why it is paused or needs review, for a person to read; empty when running.
    """

    rule_id: str
    precision: RulePrecision
    sprt: SprtResult
    severity: Severity
    status: HealthStatus = "running"
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


def sequential_sprt(outcomes: Sequence[Outcome], config: SprtConfig | None = None) -> SprtResult:
    """Run the SPRT again from the next label each time it calls the rule healthy.

    A single SPRT stops at its first boundary, so an early run of correct alerts would call a
    rule healthy for good and every wrong alert after it would go unread. Restarting after each
    "healthy" keeps reading. An "unhealthy" result ends the sequence: the rule is paused (or sent
    for review) until a person resumes it, and a resume starts a new sequence.

    Args:
        outcomes: Labelled outcomes, oldest first.
        config: Hypotheses and error rates; the defaults are p0 0.80 against p1 0.50.

    Returns:
        The current run's statistic and decision, with `n`, `crossed_at` and `started_at`
        counted from the start of `outcomes`.
    """
    config = config or SprtConfig()
    start = 0
    last_crossed: int | None = None
    while True:
        result = sprt(outcomes[start:], config)
        if result.crossed_at is not None:
            last_crossed = start + result.crossed_at
        if result.decision == "unhealthy" or (
            result.decision == "healthy" and last_crossed == len(outcomes)
        ):
            return result.model_copy(
                update={"n": last_crossed, "crossed_at": last_crossed, "started_at": start}
            )
        if result.decision == "continue":
            return result.model_copy(
                update={"n": len(outcomes), "crossed_at": last_crossed, "started_at": start}
            )
        start = start + result.n


def read_labels(records: Iterable[EventRecord]) -> tuple[AlertLabel, ...]:
    """Read every human judgement of an alert out of a run of chain records.

    Drill labels are rehearsals and are skipped. A label with no rule says nothing about any
    rule's health and is logged and skipped. A record that cannot be read is logged and skipped
    rather than failing the whole read, because rule health must not be the reason the runtime
    stops.

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


def _rule_of(record: EventRecord) -> str | None:
    """The record's rule id, or `None` (logged) when it names no rule."""
    rule_id = record.payload.get("rule_id")
    if rule_id is None or rule_id == "":
        _log.warning("rule_health_label_without_rule", seq=record.seq, kind=record.kind)
        return None
    return str(rule_id)


def _label_of(record: EventRecord) -> AlertLabel | None:
    if record.kind == "label":
        return _from_label(record)
    if record.kind == "dispute":
        return _from_dispute(record)
    return None


def _from_label(record: EventRecord) -> AlertLabel | None:
    """A `label` record's judgement; an upheld dispute's label is marked model-decided."""
    payload = record.payload
    if payload.get("drill") is True:
        return None
    outcome = _outcome_of_label(payload.get("label"))
    rule_id = None if outcome is None else _rule_of(record)
    if outcome is None or rule_id is None:
        return None
    from_dispute = payload.get("source") == DISPUTE_LABEL_SOURCE
    return AlertLabel(
        rule_id=rule_id,
        event_id=str(payload["event_id"]),
        outcome=outcome,
        source="label",
        ts=record.ts,
        decided_by="model" if from_dispute else "person",
    )


def _from_dispute(record: EventRecord) -> AlertLabel | None:
    """A reviewed dispute's judgement, which the Tier 2 model made (`worker/disputes.py`)."""
    payload = record.payload
    dispute = Dispute.model_validate(payload["dispute"])
    if dispute.outcome == "pending":
        return None  # nobody has reviewed it, so it says nothing either way
    rule_id = _rule_of(record)
    if rule_id is None:
        return None
    return AlertLabel(
        rule_id=rule_id,
        event_id=str(payload["event_id"]),
        outcome="false_positive" if dispute.outcome == "upheld" else "true_positive",
        source="dispute",
        ts=record.ts,
        decided_by="model",
    )


def _outcome_of_label(verdict: object) -> Outcome | None:
    if verdict == FALSE_ALARM:
        return "false_positive"
    if verdict == CONFIRMED:
        return "true_positive"
    return None


def _latest_per_alert(
    rule_id: str, labels: Iterable[AlertLabel], since: datetime | None = None
) -> list[AlertLabel]:
    """This rule's labels after `since`, the latest per alert, oldest first."""
    latest: dict[str, AlertLabel] = {}
    for label in sorted((x for x in labels if x.rule_id == rule_id), key=lambda x: x.ts):
        if since is None or label.ts > since:
            latest[label.event_id] = label
    return sorted(latest.values(), key=lambda x: x.ts)


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
    latest = _latest_per_alert(rule_id, labels)
    tp = sum(x.outcome == "true_positive" for x in latest)
    fp = sum(x.outcome == "false_positive" for x in latest)
    lo, hi = wilson_interval(tp, tp + fp, confidence)
    return RulePrecision(
        rule_id=rule_id,
        true_positives=tp,
        false_positives=fp,
        unlabelled=max(alerts - len(latest), 0),
        model_decided=sum(x.decided_by == "model" for x in latest),
        precision=None if tp + fp == 0 else tp / (tp + fp),
        lo=lo,
        hi=hi,
    )


def assess(
    rule_id: str,
    labels: Sequence[AlertLabel],
    *,
    severity: Severity,
    alerts: int = 0,
    config: SprtConfig | None = None,
    since: datetime | None = None,
) -> RuleHealth:
    """Measure a rule and decide whether it should be paused or sent for review.

    Args:
        rule_id: The rule to assess.
        labels: Labels of any rule; only this rule's are used.
        severity: The rule's severity. A HIGH rule is never paused: a failing test marks it
            `needs_review` and it keeps running.
        alerts: How many alerts this rule raised, for the unlabelled count.
        config: SPRT hypotheses and error rates.
        since: Only labels recorded after this moment enter the test (the last resume).
            Precision is still measured over every label.

    Returns:
        The precision, the SPRT and the status with its reason.
    """
    config = config or SprtConfig()
    ordered = _latest_per_alert(rule_id, labels, since)
    result = sequential_sprt([x.outcome for x in ordered], config)
    measured = precision_of(rule_id, labels, alerts=alerts)
    if not result.should_pause:
        return RuleHealth(rule_id=rule_id, precision=measured, sprt=result, severity=severity)
    high = severity is Severity.HIGH
    return RuleHealth(
        rule_id=rule_id,
        precision=measured,
        sprt=result,
        severity=severity,
        status="needs_review" if high else "paused",
        paused=not high,
        reason=_reason(measured, result, config, high=high),
    )


def _reason(measured: RulePrecision, result: SprtResult, config: SprtConfig, *, high: bool) -> str:
    """The sentence a person reads about why a rule was paused or needs review."""
    shown = "n/a" if measured.precision is None else f"{measured.precision:.0%}"
    head = "Needs review" if high else "Paused automatically"
    model = (
        f" ({measured.model_decided} of them decided by the dispute reviewer model, not a person)"
        if measured.model_decided
        else ""
    )
    tail = (
        "This is a HIGH-severity rule, so it keeps running; a person decides whether to fix, "
        "pause or keep it."
        if high
        else "The rule stops producing alerts until someone fixes it and resumes it."
    )
    return (
        f"{head}: of {measured.labelled} alerts judged{model}, "
        f"{measured.false_positives} were wrong (precision {shown}, "
        f"95% interval {measured.lo:.0%}-{measured.hi:.0%}). A sequential test "
        f"(p0={config.p0:.2f} against p1={config.p1:.2f}, alpha={config.alpha}, "
        f"beta={config.beta}) crossed its lower boundary at judgement "
        f"{result.crossed_at} since the rule was last started. {tail}"
    )


class RuleHealthMonitor:
    """Tracks every rule's health and holds the paused and needs-review sets.

    A LOW or MEDIUM rule is paused once: the first assessment whose test fails publishes one
    `rule_change` record (`change: paused`), and later assessments do not publish again. A HIGH
    rule is never paused; it is flagged once (`change: needs_review`) and keeps running.
    Resuming, or clearing a review flag, is a person's decision (`resume`), never automatic. The
    test then starts again from labels recorded after the resume, so the evidence that caused
    the pause cannot cause it again.

    The monitor is safe to call from several threads; the runtime reads `is_paused` per chunk.
    """

    def __init__(
        self,
        chain: EventChain | None = None,
        config: SprtConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Build a monitor.

        Args:
            chain: Evidence chain for `rule_change` records. Without one nothing is published,
                which is what the unit tests use.
            config: SPRT hypotheses and error rates.
            clock: Time source for the resume point; the system clock by default.
        """
        self._chain = chain
        self._config = config or SprtConfig()
        self._clock: Clock = clock or SystemClock()
        self._paused: dict[str, str] = {}
        self._review: dict[str, str] = {}
        self._since: dict[str, datetime] = {}
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

    def needs_review(self, rule_id: str) -> bool:
        """Whether this HIGH rule failed its test and is waiting for a person (still running)."""
        with self._lock:
            return rule_id in self._review

    def review_rules(self) -> frozenset[str]:
        """The rule ids waiting for a person's review."""
        with self._lock:
            return frozenset(self._review)

    def status(self, rule_id: str) -> HealthStatus:
        """`paused`, `needs_review` or `running`."""
        with self._lock:
            return self._status(rule_id)

    def reason(self, rule_id: str) -> str:
        """Why this rule is paused or needs review, or an empty string if it is running."""
        with self._lock:
            return self._paused.get(rule_id) or self._review.get(rule_id, "")

    def health(self, rule_id: str) -> RuleHealth | None:
        """The last assessment of this rule, if it has been assessed."""
        with self._lock:
            return self._health.get(rule_id)

    def assess(
        self,
        rule_id: str,
        labels: Sequence[AlertLabel],
        *,
        severity: Severity,
        alerts: int = 0,
    ) -> RuleHealth:
        """Assess one rule, pausing or flagging it and publishing a `rule_change` if needed.

        Args:
            rule_id: The rule to assess.
            labels: Labels of any rule; only this rule's are used.
            severity: The rule's severity; HIGH is flagged for review, never paused.
            alerts: How many alerts the rule raised, for the unlabelled count.

        Returns:
            The assessment, with the rule's standing status: a rule paused or flagged by an
            earlier call stays so until `resume`.
        """
        with self._lock:
            since = self._since.get(rule_id)
        found = assess(
            rule_id, labels, severity=severity, alerts=alerts, config=self._config, since=since
        )
        with self._lock:
            held = self._paused if found.status == "paused" else self._review
            publish = found.status != "running" and rule_id not in held
            if publish:
                held[rule_id] = found.reason
            status = self._status(rule_id)
            stored = found.model_copy(
                update={
                    "status": status,
                    "paused": status == "paused",
                    "reason": self._paused.get(rule_id) or self._review.get(rule_id, ""),
                }
            )
            self._health[rule_id] = stored
        if publish:
            self._publish(stored)
        return stored

    def assess_all(
        self,
        labels: Sequence[AlertLabel],
        *,
        severities: Mapping[str, Severity],
        alerts: Mapping[str, int] | None = None,
        rule_ids: Iterable[str] | None = None,
    ) -> tuple[RuleHealth, ...]:
        """Assess every rule that has a label (or every rule in `rule_ids`).

        Args:
            labels: Labels of any rule.
            severities: Each rule's severity. A rule missing from it is treated as HIGH, so a
                missing value can never switch a rule off.
            alerts: Alerts raised per rule, for the unlabelled counts.
            rule_ids: Rules to assess; the default is every rule that has a label.

        Returns:
            One assessment per rule, by rule id.
        """
        counts = dict(alerts or {})
        ids = sorted(set(rule_ids) if rule_ids is not None else {x.rule_id for x in labels})
        return tuple(
            self.assess(
                i, labels, severity=severities.get(i, Severity.HIGH), alerts=counts.get(i, 0)
            )
            for i in ids
        )

    def resume(self, rule_id: str, *, role: str, note: str = "") -> bool:
        """Resume a paused rule, or clear a review flag, by a person's decision.

        The test restarts from labels recorded after this moment, which is written on the
        `rule_change` record as `resumed_at`, so the old evidence cannot pause the rule again.

        Args:
            rule_id: The rule to resume.
            role: Who resumed it, for the record.
            note: What they changed or checked.

        Returns:
            True if the rule was paused or flagged and is now running clear; False otherwise.
        """
        resumed_at = self._clock.now()
        with self._lock:
            was = self._status(rule_id)
            if was == "running":
                return False
            self._paused.pop(rule_id, None)
            self._review.pop(rule_id, None)
            self._since[rule_id] = resumed_at
            found = self._health.get(rule_id)
            if found is not None:
                self._health[rule_id] = found.model_copy(
                    update={"status": "running", "paused": False, "reason": ""}
                )
        if self._chain is not None:
            self._chain.append(
                "rule_change",
                {
                    "rule_id": rule_id,
                    "change": "resumed",
                    "was": was,
                    "by": role,
                    "note": note,
                    "automatic": False,
                    "resumed_at": utc_iso(resumed_at),
                },
            )
        _log.info("rule_resumed", rule_id=rule_id, role=role, was=was)
        return True

    def _status(self, rule_id: str) -> HealthStatus:
        """The standing status; call with the lock held."""
        if rule_id in self._paused:
            return "paused"
        if rule_id in self._review:
            return "needs_review"
        return "running"

    def _publish(self, found: RuleHealth) -> None:
        if self._chain is not None:
            self._chain.append(
                "rule_change",
                {
                    "rule_id": found.rule_id,
                    "change": found.status,
                    "automatic": True,
                    "severity": found.severity.value,
                    "reason": found.reason,
                    "precision": found.precision.precision,
                    "precision_lo": found.precision.lo,
                    "precision_hi": found.precision.hi,
                    "labelled": found.precision.labelled,
                    "false_positives": found.precision.false_positives,
                    "model_decided": found.precision.model_decided,
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
            "rule_auto_paused" if found.paused else "rule_needs_review",
            rule_id=found.rule_id,
            severity=found.severity.value,
            precision=found.precision.precision,
            labelled=found.precision.labelled,
            crossed_at=found.sprt.crossed_at,
        )
