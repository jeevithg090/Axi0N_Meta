"""Rule health: measured precision, the SPRT boundaries and the auto-pause (task P4-09)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rulecam.contracts.core import Severity
from rulecam.contracts.events import Dispute
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.stats.intervals import wilson_interval
from rulecam.verify import health as health_module
from rulecam.verify.health import (
    AlertLabel,
    Outcome,
    RuleHealthMonitor,
    SprtConfig,
    assess,
    precision_of,
    read_labels,
    sequential_sprt,
    sprt,
)
from tests.fakes import EPOCH, FakeClock

RULE = "forklift_2m"
MEDIUM = Severity.MEDIUM
HIGH = Severity.HIGH


def label(
    n: int, outcome: Outcome, *, rule_id: str = RULE, event_id: str | None = None
) -> AlertLabel:
    return AlertLabel(
        rule_id=rule_id,
        event_id=event_id or f"ev-{n:03d}",
        outcome=outcome,
        source="label",
        ts=EPOCH + timedelta(seconds=n),
    )


def labels(pattern: str, *, rule_id: str = RULE) -> list[AlertLabel]:
    """`pattern` is one character per alert: `t` true positive, `f` false positive."""
    return [
        label(i, "true_positive" if c == "t" else "false_positive", rule_id=rule_id)
        for i, c in enumerate(pattern, start=1)
    ]


# --- the SPRT ---------------------------------------------------------------------------------


def test_the_boundaries_are_walds() -> None:
    config = SprtConfig(p0=0.8, p1=0.5, alpha=0.05, beta=0.10)
    assert config.upper == pytest.approx(math.log(0.95 / 0.10))
    assert config.lower == pytest.approx(math.log(0.05 / 0.90))


def test_a_hypothesis_that_is_not_a_drop_is_refused() -> None:
    with pytest.raises(ValueError, match="must be below"):
        SprtConfig(p0=0.5, p1=0.8)
    with pytest.raises(ValueError, match="must be below"):
        SprtConfig(p0=0.8, p1=0.8)


def test_each_step_is_the_log_likelihood_ratio_for_h0() -> None:
    config = SprtConfig(p0=0.8, p1=0.5)
    one_true = sprt(["true_positive"], config)
    one_false = sprt(["false_positive"], config)
    assert one_true.llr == pytest.approx(math.log(0.8 / 0.5))
    assert one_false.llr == pytest.approx(math.log(0.2 / 0.5))
    assert one_true.llr > 0  # a correct alert is evidence for health
    assert one_false.llr < 0  # a wrong one is evidence against


def test_no_labels_decides_nothing() -> None:
    result = sprt([])
    assert result.decision == "continue"
    assert result.llr == pytest.approx(0.0)
    assert result.n == 0
    assert result.crossed_at is None
    assert not result.should_pause


def test_too_few_labels_decides_nothing() -> None:
    """Three wrong alerts are not enough to cross the boundary at these error rates."""
    config = SprtConfig(p0=0.8, p1=0.5, alpha=0.05, beta=0.10)
    result = sprt(["false_positive"] * 3, config)
    assert result.decision == "continue"
    assert result.llr > config.lower
    assert not result.should_pause


def test_enough_wrong_alerts_crosses_the_lower_boundary() -> None:
    config = SprtConfig(p0=0.8, p1=0.5, alpha=0.05, beta=0.10)
    step = math.log(0.2 / 0.5)
    needed = math.ceil(config.lower / step)
    assert needed == 4  # pins the arithmetic, so a change to it is deliberate

    assert sprt(["false_positive"] * (needed - 1), config).decision == "continue"
    crossed = sprt(["false_positive"] * needed, config)
    assert crossed.decision == "unhealthy"
    assert crossed.should_pause
    assert crossed.crossed_at == needed


def test_the_test_stops_at_the_label_that_crossed() -> None:
    """Labels after the crossing are not read, which is what keeps the error rates true."""
    config = SprtConfig(p0=0.8, p1=0.5, alpha=0.05, beta=0.10)
    result = sprt(["false_positive"] * 10, config)
    assert result.crossed_at == 4
    assert result.n == 4


def test_a_good_run_crosses_the_upper_boundary_and_stops() -> None:
    config = SprtConfig(p0=0.8, p1=0.5, alpha=0.05, beta=0.10)
    step = math.log(0.8 / 0.5)
    needed = math.ceil(config.upper / step)
    result = sprt(["true_positive"] * needed, config)
    assert result.decision == "healthy"
    assert not result.should_pause
    assert result.crossed_at == needed


def test_correct_alerts_hold_a_rule_open_against_wrong_ones() -> None:
    config = SprtConfig(p0=0.8, p1=0.5, alpha=0.05, beta=0.10)
    assert sprt(["false_positive"] * 4, config).decision == "unhealthy"
    assert sprt(["true_positive", "false_positive"] * 2, config).decision == "continue"


# --- precision --------------------------------------------------------------------------------


def test_precision_is_over_labelled_alerts_with_a_wilson_interval() -> None:
    got = precision_of(RULE, labels("ttf"))
    assert (got.true_positives, got.false_positives) == (2, 1)
    assert got.labelled == 3
    assert got.precision == pytest.approx(2 / 3)
    assert (got.lo, got.hi) == wilson_interval(2, 3, 0.95)


def test_an_unlabelled_alert_is_not_counted_as_correct() -> None:
    """The one thing this must never do: treat silence as a correct alert."""
    got = precision_of(RULE, labels("tf"), alerts=400)
    assert got.labelled == 2
    assert got.unlabelled == 398
    assert got.precision == pytest.approx(0.5)
    assert got.label_share == pytest.approx(2 / 400)


def test_no_labels_gives_no_precision_and_the_full_interval() -> None:
    got = precision_of(RULE, [], alerts=12)
    assert got.precision is None
    assert (got.lo, got.hi) == (0.0, 1.0)
    assert got.unlabelled == 12
    assert got.label_share == pytest.approx(0.0)


def test_a_rule_that_never_fired_has_no_label_share() -> None:
    assert precision_of(RULE, []).label_share is None


def test_another_rule_s_labels_are_ignored() -> None:
    mixed = [*labels("tt"), *labels("ff", rule_id="helmet_required")]
    assert precision_of(RULE, mixed).labelled == 2
    assert precision_of("helmet_required", mixed).false_positives == 2


def test_one_alert_judged_twice_counts_once_and_the_latest_wins() -> None:
    """A false-alarm label and then an upheld dispute is one wrong alert, not two."""
    first = label(1, "true_positive", event_id="ev-001")
    second = label(2, "false_positive", event_id="ev-001")
    got = precision_of(RULE, [first, second])
    assert got.labelled == 1
    assert got.false_positives == 1
    assert got.true_positives == 0


# --- reading the chain ------------------------------------------------------------------------


def chain_for(tmp_path: Path) -> EventChain:
    return EventChain(tmp_path, site="site-1", clock=FakeClock())


def test_a_false_alarm_label_reads_as_a_false_positive(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    chain.append("label", {"event_id": "ev-1", "rule_id": RULE, "label": "false_alarm"})
    got = read_labels(read_chain(chain.path_for(FakeClock().now().date())))
    assert len(got) == 1
    assert got[0].outcome == "false_positive"
    assert got[0].source == "label"
    assert got[0].rule_id == RULE


def test_a_confirmed_label_reads_as_a_true_positive(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    chain.append("label", {"event_id": "ev-1", "rule_id": RULE, "label": "confirmed"})
    got = read_labels(read_chain(chain.path_for(FakeClock().now().date())))
    assert got[0].outcome == "true_positive"


def test_an_upheld_dispute_is_a_false_positive_and_a_rejected_one_is_not(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    for i, outcome in enumerate(("upheld", "rejected"), start=1):
        chain.append(
            "dispute",
            {
                "dispute_id": f"d-{i}",
                "event_id": f"ev-{i}",
                "rule_id": RULE,
                "camera_id": "cam1",
                "dispute": Dispute(
                    channel="tap", reason_text="no forklift", outcome=outcome
                ).model_dump(mode="json"),
            },
        )
    got = read_labels(read_chain(chain.path_for(FakeClock().now().date())))
    assert [x.outcome for x in got] == ["false_positive", "true_positive"]
    assert {x.source for x in got} == {"dispute"}


def test_a_pending_dispute_says_nothing_either_way(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    chain.append(
        "dispute",
        {
            "dispute_id": "d-1",
            "event_id": "ev-1",
            "rule_id": RULE,
            "camera_id": "cam1",
            "dispute": Dispute(channel="tap", reason_text="not right").model_dump(mode="json"),
        },
    )
    assert read_labels(read_chain(chain.path_for(FakeClock().now().date()))) == ()


def test_a_drill_label_is_not_a_real_alert(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    chain.append(
        "label",
        {"drill": True, "event_id": "drill-1", "rule_id": RULE, "label": "false_alarm"},
    )
    assert read_labels(read_chain(chain.path_for(FakeClock().now().date()))) == ()


def test_a_label_with_no_rule_and_an_unreadable_one_are_skipped(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    chain.append("label", {"event_id": "ev-1", "label": "false_alarm"})  # no rule_id
    chain.append("label", {"event_id": "ev-2", "rule_id": RULE, "label": "who_knows"})
    chain.append("dispute", {"dispute_id": "d-1", "event_id": "ev-3", "rule_id": RULE})
    chain.append("candidate", {"event_id": "ev-4"})
    assert read_labels(read_chain(chain.path_for(FakeClock().now().date()))) == ()


# --- assess and the monitor -------------------------------------------------------------------


def test_assess_pauses_with_a_reason_a_person_can_read() -> None:
    found = assess(RULE, labels("ffff"), severity=MEDIUM, alerts=10)
    assert found.paused
    assert "4 alerts judged" in found.reason
    assert "4 were wrong" in found.reason
    assert "crossed its lower boundary at judgement 4" in found.reason
    assert found.precision.unlabelled == 6


def test_assess_does_not_pause_on_too_little_data() -> None:
    found = assess(RULE, labels("ff"), severity=MEDIUM, alerts=50)
    assert not found.paused
    assert found.reason == ""
    assert found.sprt.decision == "continue"


def test_the_monitor_pauses_and_publishes_one_rule_change(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    monitor = RuleHealthMonitor(chain)
    found = monitor.assess(RULE, labels("ffff"), severity=MEDIUM)
    assert found.paused
    assert monitor.is_paused(RULE)
    assert monitor.paused_rules() == frozenset({RULE})
    assert "Paused automatically" in monitor.reason(RULE)

    records = [
        r for r in read_chain(chain.path_for(FakeClock().now().date())) if r.kind == "rule_change"
    ]
    assert len(records) == 1
    payload = records[0].payload
    assert payload["rule_id"] == RULE
    assert payload["change"] == "paused"
    assert payload["automatic"] is True
    assert payload["crossed_at"] == 4
    assert payload["labelled"] == 4
    assert str(payload["reason"]).startswith("Paused automatically")


def test_assessing_a_paused_rule_again_publishes_nothing_more(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    monitor = RuleHealthMonitor(chain)
    monitor.assess(RULE, labels("ffff"), severity=MEDIUM)
    again = monitor.assess(RULE, labels("ffffff"), severity=MEDIUM)
    assert again.paused
    changes = [
        r for r in read_chain(chain.path_for(FakeClock().now().date())) if r.kind == "rule_change"
    ]
    assert len(changes) == 1


def test_a_healthy_rule_is_not_paused_and_publishes_nothing(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    monitor = RuleHealthMonitor(chain)
    found = monitor.assess(RULE, labels("tttttt"), severity=MEDIUM)
    assert not found.paused
    assert not monitor.is_paused(RULE)
    assert monitor.paused_rules() == frozenset()
    assert monitor.reason(RULE) == ""
    assert not chain.path_for(FakeClock().now().date()).exists()  # nothing was written at all


def test_a_monitor_without_a_chain_still_pauses() -> None:
    monitor = RuleHealthMonitor()
    assert monitor.assess(RULE, labels("ffff"), severity=MEDIUM).paused
    assert monitor.is_paused(RULE)


def test_resuming_is_a_person_s_decision_and_is_recorded(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    monitor = RuleHealthMonitor(chain)
    monitor.assess(RULE, labels("ffff"), severity=MEDIUM)
    assert monitor.resume(RULE, role="owner", note="zone redrawn")
    assert not monitor.is_paused(RULE)
    assert monitor.reason(RULE) == ""
    health = monitor.health(RULE)
    assert health is not None
    assert not health.paused

    changes = [
        r for r in read_chain(chain.path_for(FakeClock().now().date())) if r.kind == "rule_change"
    ]
    assert [r.payload["change"] for r in changes] == ["paused", "resumed"]
    assert changes[1].payload["by"] == "owner"
    assert changes[1].payload["automatic"] is False


def test_resuming_a_rule_that_is_not_paused_does_nothing(tmp_path: Path) -> None:
    monitor = RuleHealthMonitor(chain_for(tmp_path))
    assert not monitor.resume(RULE, role="owner")


def test_assess_all_covers_every_rule_with_a_label() -> None:
    monitor = RuleHealthMonitor()
    mixed = [*labels("ffff"), *labels("tt", rule_id="helmet_required")]
    found = monitor.assess_all(
        mixed,
        severities={RULE: MEDIUM, "helmet_required": MEDIUM},
        alerts={RULE: 9, "helmet_required": 3},
    )
    assert [x.rule_id for x in found] == ["forklift_2m", "helmet_required"]
    assert found[0].paused
    assert not found[1].paused
    assert monitor.paused_rules() == frozenset({RULE})


def test_assess_all_can_be_asked_about_a_rule_with_no_labels() -> None:
    monitor = RuleHealthMonitor()
    found = monitor.assess_all([], severities={}, rule_ids=["quiet_rule"])
    assert len(found) == 1
    assert not found[0].paused
    assert found[0].precision.precision is None


def test_health_is_none_before_a_rule_is_assessed() -> None:
    assert RuleHealthMonitor().health(RULE) is None


def test_the_monitor_reports_the_configuration_it_uses() -> None:
    config = SprtConfig(p0=0.9, p1=0.6, alpha=0.01, beta=0.05)
    assert RuleHealthMonitor(config=config).config == config


def test_resuming_a_rule_the_monitor_never_assessed_still_clears_it() -> None:
    """A pause can be restored from the chain without an assessment in memory."""
    monitor = RuleHealthMonitor()
    monitor.assess(RULE, labels("ffff"), severity=MEDIUM)
    monitor._health.clear()
    assert monitor.resume(RULE, role="owner")
    assert not monitor.is_paused(RULE)


# --- review fixes: restarts, resume point, HIGH severity, missing rules, model decisions -------


def rule_changes(chain: EventChain) -> list[dict[str, object]]:
    path = chain.path_for(FakeClock().now().date())
    return [r.payload for r in read_chain(path) if r.kind == "rule_change"]


def labels_after(start: datetime, pattern: str, *, first: int = 1) -> list[AlertLabel]:
    """Labels recorded after `start`, one second apart, with fresh event ids."""
    return [
        AlertLabel(
            rule_id=RULE,
            event_id=f"late-{first + i:03d}",
            outcome="true_positive" if c == "t" else "false_positive",
            source="label",
            ts=start + timedelta(seconds=first + i),
        )
        for i, c in enumerate(pattern)
    ]


def outcomes(pattern: str) -> list[Outcome]:
    """`pattern` is one character per label: `t` true positive, `f` false positive."""
    return [x.outcome for x in labels(pattern)]


def test_the_test_restarts_after_calling_a_rule_healthy() -> None:
    result = sequential_sprt(outcomes("ttttt" + "f" * 100))
    assert result.decision == "unhealthy"
    assert result.started_at == 5
    assert result.crossed_at == 9
    assert result.n == 9


def test_a_healthy_call_on_the_last_label_is_reported_as_healthy() -> None:
    result = sequential_sprt(outcomes("t" * 10))
    assert result.decision == "healthy"
    assert result.crossed_at == 10
    assert result.started_at == 5


def test_after_a_healthy_call_the_new_run_carries_on() -> None:
    result = sequential_sprt(outcomes("tttttff"))
    assert result.decision == "continue"
    assert result.crossed_at == 5
    assert result.started_at == 5
    assert result.n == 7
    assert result.llr == pytest.approx(2 * math.log(0.2 / 0.5))


def test_an_early_healthy_call_does_not_hide_later_false_alarms() -> None:
    """Five confirmed then a hundred false alarms is a broken rule, not a healthy one."""
    found = assess(RULE, labels("ttttt" + "f" * 100), severity=MEDIUM)
    assert found.paused
    assert found.status == "paused"
    assert found.precision.precision == pytest.approx(5 / 105)
    assert "at judgement 9" in found.reason


def test_a_resume_sticks_when_no_new_labels_arrive(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    resumed_at = EPOCH + timedelta(hours=1)
    monitor = RuleHealthMonitor(chain, clock=FakeClock(resumed_at))
    old = labels("ffff")
    assert monitor.assess(RULE, old, severity=MEDIUM).paused
    assert monitor.resume(RULE, role="owner", note="zone redrawn")

    again = monitor.assess(RULE, old, severity=MEDIUM)
    assert not again.paused
    assert again.status == "running"
    assert again.sprt.n == 0
    assert again.precision.false_positives == 4  # precision still sees every label
    assert not monitor.is_paused(RULE)
    changes = rule_changes(chain)
    assert [c["change"] for c in changes] == ["paused", "resumed"]
    assert changes[1]["resumed_at"] == "2026-10-30T13:00:00Z"
    assert changes[1]["was"] == "paused"


def test_new_false_alarms_after_a_resume_can_pause_again(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    resumed_at = EPOCH + timedelta(hours=1)
    monitor = RuleHealthMonitor(chain, clock=FakeClock(resumed_at))
    monitor.assess(RULE, labels("ffff"), severity=MEDIUM)
    monitor.resume(RULE, role="owner")

    three_more = [*labels("ffff"), *labels_after(resumed_at, "fff")]
    assert not monitor.assess(RULE, three_more, severity=MEDIUM).paused
    four_more = [*labels("ffff"), *labels_after(resumed_at, "ffff")]
    found = monitor.assess(RULE, four_more, severity=MEDIUM)
    assert found.paused
    assert found.sprt.crossed_at == 4
    assert [c["change"] for c in rule_changes(chain)] == ["paused", "resumed", "paused"]


def test_a_high_severity_rule_is_flagged_for_review_and_keeps_running() -> None:
    found = assess(RULE, labels("ffff"), severity=HIGH)
    assert found.status == "needs_review"
    assert not found.paused
    assert found.reason.startswith("Needs review")
    assert "HIGH-severity rule, so it keeps running" in found.reason


def test_a_low_severity_rule_is_paused() -> None:
    assert assess(RULE, labels("ffff"), severity=Severity.LOW).status == "paused"


def test_the_monitor_never_pauses_a_high_rule(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    monitor = RuleHealthMonitor(chain)
    found = monitor.assess(RULE, labels("ffff"), severity=HIGH)
    assert found.status == "needs_review"
    assert not monitor.is_paused(RULE)
    assert monitor.paused_rules() == frozenset()
    assert monitor.needs_review(RULE)
    assert monitor.review_rules() == frozenset({RULE})
    assert monitor.status(RULE) == "needs_review"
    assert monitor.reason(RULE).startswith("Needs review")

    monitor.assess(RULE, labels("ffffff"), severity=HIGH)
    changes = rule_changes(chain)
    assert len(changes) == 1  # flagged once, like a pause
    assert changes[0]["change"] == "needs_review"
    assert changes[0]["severity"] == "high"
    assert changes[0]["automatic"] is True


def test_a_person_clears_a_review_flag_by_resuming(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    monitor = RuleHealthMonitor(chain, clock=FakeClock(EPOCH + timedelta(hours=1)))
    monitor.assess(RULE, labels("ffff"), severity=HIGH)
    assert monitor.resume(RULE, role="safety_lead", note="checked the zone")
    assert monitor.status(RULE) == "running"
    assert monitor.reason(RULE) == ""
    assert monitor.assess(RULE, labels("ffff"), severity=HIGH).status == "running"
    changes = rule_changes(chain)
    assert [c["change"] for c in changes] == ["needs_review", "resumed"]
    assert changes[1]["was"] == "needs_review"


def test_a_rule_with_no_known_severity_is_treated_as_high() -> None:
    monitor = RuleHealthMonitor()
    found = monitor.assess_all(labels("ffff"), severities={})
    assert found[0].status == "needs_review"
    assert found[0].severity is HIGH
    assert monitor.paused_rules() == frozenset()


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def info(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


def test_a_label_whose_rule_is_none_is_skipped_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(health_module, "_log", recorder)
    chain = chain_for(tmp_path)
    chain.append("label", {"event_id": "ev-1", "rule_id": None, "label": "false_alarm"})
    chain.append(
        "dispute",
        {
            "dispute_id": "d-1",
            "event_id": "ev-2",
            "rule_id": None,
            "camera_id": "cam1",
            "dispute": Dispute(
                channel="tap", reason_text="no forklift", outcome="upheld"
            ).model_dump(mode="json"),
        },
    )
    got = read_labels(read_chain(chain.path_for(FakeClock().now().date())))
    assert got == ()  # neither became a rule called "None"
    assert [e for e, _ in recorder.events] == ["rule_health_label_without_rule"] * 2


def test_dispute_judgements_are_marked_as_model_decided(tmp_path: Path) -> None:
    chain = chain_for(tmp_path)
    chain.append("label", {"event_id": "ev-1", "rule_id": RULE, "label": "false_alarm"})
    chain.append(
        "label",
        {
            "event_id": "ev-2",
            "rule_id": RULE,
            "label": "false_alarm",
            "source": "worker_dispute",
            "dispute_id": "d-2",
        },
    )
    chain.append(
        "dispute",
        {
            "dispute_id": "d-3",
            "event_id": "ev-3",
            "rule_id": RULE,
            "camera_id": "cam1",
            "dispute": Dispute(
                channel="tap", reason_text="no forklift", outcome="rejected"
            ).model_dump(mode="json"),
        },
    )
    got = read_labels(read_chain(chain.path_for(FakeClock().now().date())))
    assert [x.decided_by for x in got] == ["person", "model", "model"]
    assert precision_of(RULE, got).model_decided == 2


def test_the_reason_says_how_many_judgements_the_model_made() -> None:
    model_made = [x.model_copy(update={"decided_by": "model"}) for x in labels("ff")]
    found = assess(RULE, [*model_made, *labels("ffff")[2:]], severity=MEDIUM)
    assert found.paused
    assert "2 of them decided by the dispute reviewer model, not a person" in found.reason
