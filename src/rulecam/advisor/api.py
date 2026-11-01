"""`/advice`: checked advice cards, accept-and-pre-register, and sealed causes (task P3-10).

- `GET /advice`, `GET /advice/{id}`: cards with their recompute results. Numbers Glimmer could
  not reproduce are listed in `hidden` and must not be shown; `flagged` marks such a card.
- `POST /advice/{id}/accept` (owner-only): P4's measurement plan is pre-registered on the chain,
  then the card becomes `measuring`; the reply carries the chain seq ("pre-registered at event N").
- `GET /advice/seals`: every sealed cause in today's chain and whether its reveal matches.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel

from rulecam.advisor.ledger import AdviceLedger, PreregistrationError
from rulecam.advisor.recompute import NumberCheck, RecomputeReport, with_recompute
from rulecam.advisor.seal import SealStatus, seal_ids, seal_status
from rulecam.bus import Bus, Topic
from rulecam.capture.pair_phone import require_owner
from rulecam.contracts import Clock, MeasurementPlan, Recommendation, Verdict
from rulecam.evidence.chain import EventChain, read_chain

MAX_CARDS = 64
AdviceId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,96}$")]
PlanFactory = Callable[[Recommendation], MeasurementPlan]


class AdviceCard(BaseModel):
    """One card as the UI renders it."""

    recommendation: Recommendation
    checks: list[NumberCheck]
    hidden: list[str]
    flagged: bool
    measurement_plan: MeasurementPlan | None = None
    preregistered_seq: int | None = None
    verdict: Verdict | None = None


class AcceptResult(BaseModel):
    """Reply of `POST /advice/{id}/accept`."""

    recommendation: Recommendation
    measurement_plan: MeasurementPlan
    preregistered_seq: int


class AdviceStore:
    """Thread-safe, bounded store of checked cards."""

    def __init__(self, max_cards: int = MAX_CARDS) -> None:
        """Create an empty store holding at most `max_cards` cards (oldest dropped)."""
        self._max = max_cards
        self._cards: dict[str, AdviceCard] = {}
        self._lock = threading.Lock()

    def add(self, recommendation: Recommendation, report: RecomputeReport) -> AdviceCard:
        """Store a card with its recompute report (totals are copied onto the card)."""
        card = AdviceCard(
            recommendation=with_recompute(recommendation, report),
            checks=list(report.checks),
            hidden=sorted(report.hidden),
            flagged=report.flagged,
        )
        with self._lock:
            self._cards.pop(recommendation.id, None)
            self._cards[recommendation.id] = card
            while len(self._cards) > self._max:
                self._cards.pop(next(iter(self._cards)))
        return card

    def get(self, advice_id: str) -> AdviceCard:
        """Return one card (404 if unknown)."""
        with self._lock:
            card = self._cards.get(advice_id)
        if card is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no advice {advice_id!r}")
        return card

    def set_verdict(self, advice_id: str, verdict: Verdict) -> AdviceCard:
        """Attach the final "did it work?" verdict to a card (404 if unknown)."""
        card = self.get(advice_id).model_copy(update={"verdict": verdict})
        self.replace(card)
        return card

    def replace(self, card: AdviceCard) -> None:
        """Store an updated card."""
        with self._lock:
            self._cards[card.recommendation.id] = card

    def all(self) -> list[AdviceCard]:
        """Every card, newest first."""
        with self._lock:
            return list(reversed(self._cards.values()))


def advice_router(
    store: AdviceStore,
    chain: EventChain,
    bus: Bus,
    clock: Clock,
    *,
    plan_factory: PlanFactory | None = None,
) -> APIRouter:
    """Build the `/advice` router.

    Args:
        store: The checked cards.
        chain: The site's evidence chain (pre-registrations and seals).
        bus: Receives `advice.new` when a card changes.
        clock: Picks today's chain file for `/advice/seals`.
        plan_factory: P4's measurement-plan builder; accepting answers 503 without it.

    Returns:
        The router.
    """
    router = APIRouter(prefix="/advice", tags=["advice"])
    ledger = AdviceLedger(chain)

    @router.get("")
    def list_advice() -> list[AdviceCard]:
        return store.all()

    @router.get("/seals")
    def seals() -> list[SealStatus]:
        path = chain.path_for(clock.now().date())
        records = read_chain(path) if path.is_file() else []
        return [seal_status(records, sid) for sid in seal_ids(records)]

    @router.get("/{advice_id}")
    def get_advice(advice_id: AdviceId) -> AdviceCard:
        return store.get(advice_id)

    @router.post("/{advice_id}/accept", dependencies=[Depends(require_owner)])
    def accept(advice_id: AdviceId) -> AcceptResult:
        card = store.get(advice_id)
        if plan_factory is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "no measurement planner (P4-08) is running"
            )
        try:
            rec, plan, record = ledger.accept(
                card.recommendation, plan_factory(card.recommendation)
            )
        except PreregistrationError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        store.replace(
            card.model_copy(
                update={
                    "recommendation": rec,
                    "measurement_plan": plan,
                    "preregistered_seq": record.seq,
                }
            )
        )
        bus.publish(Topic.ADVICE_NEW, rec)
        return AcceptResult(recommendation=rec, measurement_plan=plan, preregistered_seq=record.seq)

    return router
