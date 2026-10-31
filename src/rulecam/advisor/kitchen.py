"""Site Advisor kitchen station type and playbook (task P2-S3, SA §6).

Kitchen pass-window station type watches:
- Plate wait on the pass window
- Ticket time (POS + pass, via Little's law: W = L / lambda)
- Station crowding against covers
- Handwash compliance against load (safety-flow link)
- Minutes the handsink was blocked
- Dish-pit backlog
- Food-contact prep surface sanitizing cadence (four-hour cadence)

All hypothesis statements are code-controlled and contain no raw digits (assert_no_digits).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rulecam.advisor.hypotheses import Hypothesis

# Catalogue hypotheses for kitchen and pass-window stations (SA §6).
# None of these statements may contain raw digits.
KITCHEN_HYPOTHESES: Final[tuple[Hypothesis, ...]] = (
    Hypothesis(
        id="pass_unattended",
        statement="Plates waited unattended on the pass window before pickup.",
        metric="pass_wait",
        threshold=2.0,
        unit="min",
        keywords=("pass", "plate", "wait", "pickup", "unattended", "runner"),
    ),
    Hypothesis(
        id="ticket_time_high",
        statement="Order ticket times exceeded service targets during peak volume.",
        metric="ticket_time",
        threshold=15.0,
        unit="min",
        keywords=("ticket", "time", "pos", "littles_law", "little", "delay"),
    ),
    Hypothesis(
        id="station_crowding",
        statement="Cook line and pass stations experienced crowding relative to covers.",
        metric="station_crowding",
        threshold=0.7,
        unit="share",
        keywords=("crowd", "covers", "line", "station", "congestion"),
    ),
    Hypothesis(
        id="handwash_drop_under_load",
        statement="Handwash frequency fell below compliance targets during peak orders.",
        metric="handwash_compliance",
        threshold=0.85,
        unit="share",
        keywords=("handwash", "hygiene", "wash", "compliance", "sink"),
    ),
    Hypothesis(
        id="handsink_blocked",
        statement="The handwashing sink was blocked by carts or staged prep.",
        metric="handsink_blocked_min",
        threshold=3.0,
        unit="min",
        keywords=("handsink", "sink", "blocked", "cart", "prep", "obstruct"),
    ),
    Hypothesis(
        id="dish_pit_backlog",
        statement="Bus tubs and ware backlogged at the dish pit.",
        metric="dish_pit_backlog",
        threshold=5.0,
        unit="count",
        keywords=("dish", "dishpit", "pit", "backlog", "ware", "tub"),
    ),
    Hypothesis(
        id="cleaning_cadence_exceeded",
        statement="Food-contact prep surfaces went past the four-hour sanitizing cadence.",
        metric="cleaning_cadence_hours",
        threshold=4.0,
        unit="h",
        keywords=("clean", "cleaning", "cadence", "sanitiz", "surface"),
    ),
)

KITCHEN_METRICS_CATALOGUE: Final[frozenset[str]] = frozenset(
    {
        "pass_wait",
        "plate_wait",
        "ticket_time",
        "station_crowding",
        "handwash_compliance",
        "handsink_blocked_min",
        "dish_pit_backlog",
        "cleaning_cadence_hours",
        "pickup_unattended_share",
    }
)


def littles_law_ticket_time(wip_orders: float, throughput_orders_per_minute: float) -> float:
    """Compute average ticket time W (minutes) from WIP orders L and throughput lambda.

    Little's Law: L = lambda * W  =>  W = L / lambda.

    Args:
        wip_orders: Average number of active/queued orders in process (L).
        throughput_orders_per_minute: Departure/completion rate in orders/minute (lambda).

    Returns:
        Average ticket time W in minutes.

    Raises:
        ValueError: If throughput is <= 0 or wip_orders is negative.
    """
    if throughput_orders_per_minute <= 0.0:
        msg = (
            f"throughput_orders_per_minute must be strictly positive, "
            f"got {throughput_orders_per_minute}"
        )
        raise ValueError(msg)
    if wip_orders < 0.0:
        msg = f"wip_orders cannot be negative, got {wip_orders}"
        raise ValueError(msg)
    return wip_orders / throughput_orders_per_minute


@dataclass(frozen=True)
class KitchenHygieneGuardResult:
    """Assessment of kitchen handwash compliance guardrail."""

    compliant: bool
    handwash_compliance: float
    threshold: float
    guard_rule_id: str = "kitchen-handwash-compliance"
    reason: str = ""


def check_kitchen_hygiene_guard(
    handwash_compliance: float,
    threshold: float = 0.85,
) -> KitchenHygieneGuardResult:
    """Evaluate handwash compliance against the kitchen hygiene guardrail.

    In commercial kitchens, rush hour order volume frequently compresses
    handwashing compliance. A staffing intervention (such as host/busser running
    the pass window when waitlist is empty) must maintain or improve hygiene
    cadence.
    """
    compliant = handwash_compliance >= threshold
    if not compliant:
        reason = (
            f"handwash compliance fell below hygiene guardrail threshold "
            f"({handwash_compliance:.1%} < {threshold:.1%}); staffing changes must "
            f"protect handwashing cadence"
        )
    else:
        reason = "handwash compliance satisfies hygiene guardrail"
    return KitchenHygieneGuardResult(
        compliant=compliant,
        handwash_compliance=handwash_compliance,
        threshold=threshold,
        reason=reason,
    )
