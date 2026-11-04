"""The HQ scorecard: every store, every rule, and what to fix first (store level only).

Numbers are honest in three ways:

- **BLIND is not compliant.** An outcome that overlaps a camera's BLIND time counts in neither
  the numerator nor the denominator (`perception.coverage.tally_compliance`). Time with no
  video at all is BLIND too (`no_video`).
- **Small crews are not singled out.** Below `MIN_OUTCOMES` watched outcomes a store's rate is
  not shown (`too_few`), so a two-person kitchen's number cannot point at one cook.
- **"Below the chain" needs evidence.** A store is flagged only when the top of its Wilson 95%
  interval is below the chain's pooled rate.

Guardrail (rule R13): the inputs are `RuleOutcome`s (rule, camera, time span) and camera
coverage. Nothing here sees a track, a person or a worker, and nothing groups by one.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from rulecam.chain.errors import ObservationsError
from rulecam.chain.models import (
    SEVERITY_ORDER,
    ChainObservations,
    ChainScorecard,
    FittedRule,
    FixItem,
    RuleScore,
    ScoreStatus,
    StoreFit,
    StoreObservations,
)
from rulecam.perception.coverage import ComplianceTally, CoverageSegment, tally_compliance
from rulecam.perception.intervals import Interval, clip, complement, total_seconds
from rulecam.stats.intervals import wilson_interval

MIN_OUTCOMES: Final = 5
"""Watched outcomes a store needs before its rate for a rule is shown (small-crew floor)."""

BLIND_LIMIT: Final = 0.2
"""A camera BLIND for more than this share of the window makes the rule's numbers untrusted."""

_EPSILON_S: Final = 1e-6

FixKind = Literal["setup", "camera", "compliance"]
_KIND_ORDER: Final[dict[str, int]] = {"setup": 0, "camera": 1, "compliance": 2}
_FIX_KIND: Final[dict[str, FixKind]] = {
    "not_ready": "setup",
    "camera_blind": "camera",
    "below_chain": "compliance",
}


@dataclass(frozen=True)
class CameraCoverage:
    """One camera over the window: watched share, main BLIND reason and BLIND intervals."""

    share: float
    reason: str | None
    blind: tuple[Interval, ...]


@dataclass(frozen=True)
class _Cell:
    tally: ComplianceTally
    camera: str | None
    coverage: CameraCoverage | None


def camera_coverage(segments: Sequence[CoverageSegment], window: Interval) -> CameraCoverage:
    """Watched share, the main BLIND reason and the BLIND intervals of one camera.

    Args:
        segments: The camera's watched-time segments.
        window: The scoring window.

    Returns:
        The coverage. Time with no segment is BLIND with reason `no_video`.
    """
    spans = [(Interval(start=s.start, end=s.end), s) for s in segments]
    watched = clip([span for span, s in spans if s.watched], window)
    blind = complement(watched, window)
    reasons: dict[str, float] = defaultdict(float)
    for span, segment in spans:
        if not segment.watched:
            key = str(segment.reason) if segment.reason is not None else "unknown"
            reasons[key] += total_seconds(clip([span], window))
    explained = sum(reasons.values())
    unexplained = total_seconds(blind) - explained
    if unexplained > _EPSILON_S:
        reasons["no_video"] += unexplained
    reason = max(reasons, key=lambda k: reasons[k]) if reasons else None
    return CameraCoverage(
        share=total_seconds(watched) / window.seconds, reason=reason, blind=tuple(blind)
    )


def _cell(fitted: FittedRule, observed: StoreObservations, window: Interval) -> _Cell:
    coverage = {
        camera: camera_coverage(observed.coverage.get(camera, ()), window)
        for camera in fitted.cameras
    }
    outcomes = [
        o
        for o in observed.outcomes
        if o.rule_id == fitted.rule_id and (not fitted.cameras or o.camera_id in fitted.cameras)
    ]
    tally = tally_compliance(outcomes, {c: v.blind for c, v in coverage.items()})
    worst = min(coverage, key=lambda c: coverage[c].share) if coverage else None
    return _Cell(tally=tally, camera=worst, coverage=coverage[worst] if worst else None)


def _pooled(cells: Sequence[_Cell]) -> float | None:
    compliant = sum(c.tally.compliant for c in cells)
    watched = compliant + sum(c.tally.violations for c in cells)
    return compliant / watched if watched else None


def _score(store_id: str, fitted: FittedRule, cell: _Cell | None, chain: float | None) -> RuleScore:
    """One rule at one store.

    Args:
        store_id: The store.
        fitted: The rule's fit at the store.
        cell: Its tally and coverage, None when the rule does not run there.
        chain: The chain's pooled rate for the rule.

    Returns:
        The score; `rate`, `lo` and `hi` stay None below the small-crew floor.
    """
    if cell is None:
        return RuleScore(store_id=store_id, rule_id=fitted.rule_id, status="not_ready")
    tally = cell.tally
    watched = tally.compliant + tally.violations
    shown = watched >= MIN_OUTCOMES
    lo, hi = wilson_interval(tally.compliant, watched) if shown else (None, None)
    share = cell.coverage.share if cell.coverage else None
    status: ScoreStatus
    if share is not None and share < 1.0 - BLIND_LIMIT:
        status = "camera_blind"
    elif not shown:
        status = "too_few"
    elif chain is not None and hi is not None and hi < chain:
        status = "below_chain"
    else:
        status = "ok"
    return RuleScore(
        store_id=store_id,
        rule_id=fitted.rule_id,
        status=status,
        compliant=tally.compliant,
        violations=tally.violations,
        blind=tally.blind,
        rate=tally.rate if shown else None,
        lo=lo,
        hi=hi,
        watched_share=share,
        blind_camera=cell.camera,
        blind_reason=cell.coverage.reason if cell.coverage else None,
    )


def _check(fits: Sequence[StoreFit], observations: ChainObservations) -> None:
    versions = {(f.pack_id, f.pack_version) for f in fits}
    if len(versions) != 1:
        msg = f"every store must run the same pack version to be compared, got {sorted(versions)}"
        raise ObservationsError(msg)
    unknown = sorted(set(observations.stores) - {f.store_id for f in fits})
    if unknown:
        msg = f"observations for stores with no fit: {unknown}"
        raise ObservationsError(msg)


def chain_scorecard(fits: Sequence[StoreFit], observations: ChainObservations) -> ChainScorecard:
    """Score every store's rules over the window and list what to fix first.

    Args:
        fits: Every store's fit of one pack version.
        observations: What the stores reported, all of one provenance.

    Returns:
        The scorecard.

    Raises:
        ObservationsError: The fits are of different pack versions, or observations name a
            store with no fit.
    """
    _check(fits, observations)
    window = Interval(start=observations.start, end=observations.end)
    cells: dict[tuple[str, str], _Cell] = {}
    for fit in fits:
        observed = observations.stores.get(fit.store_id, StoreObservations())
        for fitted in fit.rules:
            if fitted.status == "ready":
                cells[fit.store_id, fitted.rule_id] = _cell(fitted, observed, window)
    rule_ids = [r.rule_id for r in fits[0].rules] if fits else []
    chain = {rid: _pooled([c for (_, r), c in cells.items() if r == rid]) for rid in rule_ids}
    scores = tuple(
        _score(fit.store_id, rule, cells.get((fit.store_id, rule.rule_id)), chain[rule.rule_id])
        for fit in fits
        for rule in fit.rules
    )
    return ChainScorecard(
        pack_id=fits[0].pack_id if fits else "",
        pack_version=fits[0].pack_version if fits else 0,
        provenance=observations.provenance,
        start=observations.start,
        end=observations.end,
        chain_rates=chain,
        scores=scores,
        fix_first=fix_first(fits, scores, chain),
    )


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _fix_says(item: RuleScore, chain: float | None) -> str:
    if item.status == "camera_blind":
        return (
            f"camera {item.blind_camera} watched only {_pct(item.watched_share)} of the window "
            f"({item.blind_reason}). Its results do not count until the camera is fixed."
        )
    return (
        f"{_pct(item.rate)} compliant (95% CI {_pct(item.lo)} to {_pct(item.hi)}) against the "
        f"chain's {_pct(chain)}. Check the station first: equipment, layout, signage."
    )


def fix_first(
    fits: Sequence[StoreFit], scores: Sequence[RuleScore], chain: Mapping[str, float | None]
) -> tuple[FixItem, ...]:
    """What HQ should get fixed, most urgent first.

    Highest severity first; within a severity, rules that do not run, then BLIND cameras, then
    stores below the chain.

    Args:
        fits: Every store's fit.
        scores: The scores.
        chain: The chain's pooled rate per rule.

    Returns:
        The items, most urgent first.
    """
    fitted = {(f.store_id, r.rule_id): (f, r) for f in fits for r in f.rules}
    items: list[FixItem] = []
    for item in scores:
        fit, rule = fitted[item.store_id, item.rule_id]
        kind = _FIX_KIND.get(item.status)
        if kind is None:
            continue
        detail = (
            f"not running. {rule.says}"
            if kind == "setup"
            else _fix_says(item, chain.get(item.rule_id))
        )
        items.append(
            FixItem(
                store_id=fit.store_id,
                rule_id=rule.rule_id,
                kind=kind,
                severity=rule.severity,
                says=f"{fit.store_name} ({fit.store_id}), {rule.rule_id}: {detail}",
            )
        )
    return tuple(
        sorted(items, key=lambda i: (-SEVERITY_ORDER[i.severity], _KIND_ORDER[i.kind], i.says))
    )
