"""`/plans`: a minimal in-memory plan registry (create, list, get, approve) until P2 lands.

Plans are `SitePlan` contracts. Creating and approving are owner-only (loopback); approving
publishes `plan.updated`. The registry holds a bounded number of plans.
"""

from __future__ import annotations

import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel

from rulecam.bus import Bus, Topic
from rulecam.capture.pair_phone import require_owner
from rulecam.contracts import SitePlan

MAX_PLANS = 64
PlanId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]


class PlanSummary(BaseModel):
    """One row of `GET /plans`."""

    plan_id: str
    version: int
    site_name: str
    approved: bool


class PlanRegistry:
    """Thread-safe, bounded store of submitted plans and their approval state."""

    def __init__(self, max_plans: int = MAX_PLANS) -> None:
        """Create an empty registry holding at most `max_plans` plans."""
        self._max = max_plans
        self._plans: dict[str, tuple[SitePlan, bool]] = {}
        self._lock = threading.Lock()

    def put(self, plan: SitePlan) -> PlanSummary:
        """Store (or replace, un-approving) a plan.

        Raises:
            HTTPException: 409 when the registry is full.
        """
        with self._lock:
            if plan.plan_id not in self._plans and len(self._plans) >= self._max:
                raise HTTPException(status.HTTP_409_CONFLICT, "plan registry is full")
            self._plans[plan.plan_id] = (plan, False)
        return _summary(plan, approved=False)

    def get(self, plan_id: str) -> tuple[SitePlan, bool]:
        """Return a plan and whether it is approved.

        Raises:
            HTTPException: 404 for an unknown plan.
        """
        with self._lock:
            found = self._plans.get(plan_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no plan {plan_id!r}")
        return found

    def approve(self, plan_id: str) -> SitePlan:
        """Mark a plan approved and return it (404 if unknown)."""
        plan, _ = self.get(plan_id)
        with self._lock:
            self._plans[plan_id] = (plan, True)
        return plan

    def summaries(self) -> list[PlanSummary]:
        """Return every plan, in submission order."""
        with self._lock:
            items = list(self._plans.values())
        return [_summary(plan, approved=ok) for plan, ok in items]


def _summary(plan: SitePlan, *, approved: bool) -> PlanSummary:
    return PlanSummary(
        plan_id=plan.plan_id, version=plan.version, site_name=plan.site_name, approved=approved
    )


def plans_router(registry: PlanRegistry, bus: Bus) -> APIRouter:
    """Build the `/plans` router.

    Args:
        registry: Where plans are kept.
        bus: Receives `plan.updated` on approval.

    Returns:
        The router.
    """
    router = APIRouter(prefix="/plans", tags=["plans"])
    owner = [Depends(require_owner)]

    @router.get("")
    def list_plans() -> list[PlanSummary]:
        return registry.summaries()

    @router.post("", status_code=status.HTTP_201_CREATED, dependencies=owner)
    def create_plan(plan: SitePlan) -> PlanSummary:
        return registry.put(plan)

    @router.get("/{plan_id}")
    def get_plan(plan_id: PlanId) -> SitePlan:
        return registry.get(plan_id)[0]

    @router.post("/{plan_id}/approve", dependencies=owner)
    def approve_plan(plan_id: PlanId) -> PlanSummary:
        plan = registry.approve(plan_id)
        bus.publish(Topic.PLAN_UPDATED, plan)
        return _summary(plan, approved=True)

    return router
