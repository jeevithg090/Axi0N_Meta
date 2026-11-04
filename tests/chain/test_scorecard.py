"""The HQ scorecard: BLIND kept out, the small-crew floor, below-chain evidence, fix-first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rulecam.chain import (
    ChainObservations,
    FittedRule,
    ObservationsError,
    StoreFit,
    StoreObservations,
    chain_scorecard,
)
from rulecam.chain.scorecard import MIN_OUTCOMES, camera_coverage
from rulecam.contracts.core import Provenance, Severity
from rulecam.perception.coverage import CoverageReason, CoverageSegment, RuleOutcome
from rulecam.perception.intervals import Interval

T0 = datetime(2026, 11, 2, 8, tzinfo=UTC)
T1 = T0 + timedelta(hours=10)
WINDOW = Interval(start=T0, end=T1)


def _rule(rule_id: str = "wash", *, status: str = "ready", severity: str = "medium") -> FittedRule:
    return FittedRule(
        rule_id=rule_id,
        status=status,
        says="Runs on cam." if status == "ready" else "Needs setup: map slot 'x'",
        severity=Severity(severity),
        cameras=("cam",),
    )


def _fit(store_id: str, *rules: FittedRule, version: int = 1) -> StoreFit:
    return StoreFit(
        store_id=store_id,
        store_name=store_id.title(),
        pack_id="pack",
        pack_version=version,
        pack_digest="0" * 64,
        rules=rules or (_rule(),),
    )


def _outcomes(compliant: int, violations: int, *, rule_id: str = "wash") -> list[RuleOutcome]:
    out = []
    for i in range(compliant + violations):
        start = T0 + timedelta(minutes=5 * i)
        out.append(
            RuleOutcome(
                rule_id=rule_id,
                camera_id="cam",
                start=start,
                end=start + timedelta(seconds=30),
                compliant=i < compliant,
            )
        )
    return out


def _watched(*blind: tuple[float, float, CoverageReason]) -> dict[str, tuple[CoverageSegment, ...]]:
    """The camera watched all window except the `(from_h, to_h, reason)` spans."""
    cuts = sorted(blind)
    segments = []
    at = T0
    for start_h, end_h, reason in cuts:
        start, end = T0 + timedelta(hours=start_h), T0 + timedelta(hours=end_h)
        if at < start:
            segments.append(CoverageSegment(start=at, end=start, watched=True))
        segments.append(CoverageSegment(start=start, end=end, watched=False, reason=reason))
        at = end
    if at < T1:
        segments.append(CoverageSegment(start=at, end=T1, watched=True))
    return {"cam": tuple(segments)}


def _obs(**stores: StoreObservations) -> ChainObservations:
    return ChainObservations(provenance=Provenance.RECORDED, start=T0, end=T1, stores=stores)


def _store(compliant: int, violations: int) -> StoreObservations:
    return StoreObservations(outcomes=tuple(_outcomes(compliant, violations)), coverage=_watched())


def test_rates_and_the_chain_rate_are_pooled_per_rule() -> None:
    card = chain_scorecard([_fit("a"), _fit("b")], _obs(a=_store(18, 2), b=_store(9, 1)))
    scores = {s.store_id: s for s in card.scores}
    assert scores["a"].rate == pytest.approx(0.9)
    assert scores["a"].lo is not None
    assert scores["a"].hi is not None
    assert scores["a"].lo < 0.9 < scores["a"].hi
    assert card.chain_rates == {"wash": pytest.approx(27 / 30)}
    assert {s.status for s in card.scores} == {"ok"}
    assert card.fix_first == ()
    assert card.provenance is Provenance.RECORDED


def test_outcomes_in_blind_time_count_in_neither_side() -> None:
    blind = StoreObservations(
        outcomes=tuple(_outcomes(4, 8)),  # the 8 violations fall between 0:20 and 1:00
        coverage=_watched((0.3, 1.0, CoverageReason.FROZEN)),
    )
    score = chain_scorecard([_fit("a")], _obs(a=blind)).scores[0]
    assert (score.compliant, score.violations, score.blind) == (4, 0, 8)
    assert score.status == "too_few"  # 4 watched outcomes are under the floor


def test_below_the_small_crew_floor_no_rate_is_shown() -> None:
    score = chain_scorecard([_fit("a")], _obs(a=_store(MIN_OUTCOMES - 1, 0))).scores[0]
    assert score.status == "too_few"
    assert (score.rate, score.lo, score.hi) == (None, None, None)
    assert score.compliant == MIN_OUTCOMES - 1


def test_a_store_is_below_the_chain_only_with_evidence() -> None:
    fits = [_fit("a"), _fit("b"), _fit("c")]
    card = chain_scorecard(fits, _obs(a=_store(95, 5), b=_store(95, 5), c=_store(30, 20)))
    scores = {s.store_id: s for s in card.scores}
    assert scores["c"].status == "below_chain"
    chain = card.chain_rates["wash"]
    assert scores["c"].hi is not None
    assert chain is not None
    assert scores["c"].hi < chain
    close = chain_scorecard(fits, _obs(a=_store(9, 1), b=_store(9, 1), c=_store(7, 3)))
    assert {s.store_id: s.status for s in close.scores}["c"] == "ok"  # 10 outcomes: no evidence
    assert [i.kind for i in card.fix_first] == ["compliance"]
    assert "60% compliant" in card.fix_first[0].says


def test_a_camera_blind_too_long_untrusts_the_numbers() -> None:
    blind = StoreObservations(
        outcomes=tuple(_outcomes(20, 0)),
        coverage=_watched((2.0, 5.0, CoverageReason.MOVED)),
    )
    card = chain_scorecard([_fit("a")], _obs(a=blind))
    score = card.scores[0]
    assert score.status == "camera_blind"
    assert score.watched_share == pytest.approx(0.7)
    assert (score.blind_camera, score.blind_reason) == ("cam", "moved")
    assert "watched only 70%" in card.fix_first[0].says


def test_time_with_no_video_is_blind() -> None:
    coverage = camera_coverage((), WINDOW)
    assert coverage.share == 0.0
    assert coverage.reason == "no_video"
    score = chain_scorecard([_fit("a")], _obs()).scores[0]
    assert score.status == "camera_blind"
    assert score.blind_reason == "no_video"


def test_rules_that_do_not_run_are_fix_items_ranked_by_severity() -> None:
    fits = [
        _fit(
            "a",
            _rule("wash", status="needs_setup"),
            _rule("exit", status="blocked", severity="high"),
        ),
        _fit("b", _rule("wash"), _rule("exit", severity="high")),
    ]
    card = chain_scorecard(fits, _obs(b=_store(10, 0)))
    statuses = {(s.store_id, s.rule_id): s.status for s in card.scores}
    assert statuses["a", "wash"] == statuses["a", "exit"] == "not_ready"
    assert statuses["b", "exit"] == "too_few"  # watched, but no outcomes: nothing to fix
    assert [(i.store_id, i.rule_id, i.kind) for i in card.fix_first] == [
        ("a", "exit", "setup"),  # high severity first
        ("a", "wash", "setup"),
    ]
    assert card.fix_first[0].severity is Severity.HIGH
    assert card.fix_first[0].says.startswith("A (a), exit: not running.")


def test_only_one_pack_version_can_be_compared() -> None:
    with pytest.raises(ObservationsError, match="same pack version"):
        chain_scorecard([_fit("a"), _fit("b", version=2)], _obs())


def test_observations_must_belong_to_fitted_stores() -> None:
    with pytest.raises(ObservationsError, match="no fit"):
        chain_scorecard([_fit("a")], _obs(z=StoreObservations()))


def test_the_window_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="end after it starts"):
        ChainObservations(provenance=Provenance.RECORDED, start=T1, end=T0)
