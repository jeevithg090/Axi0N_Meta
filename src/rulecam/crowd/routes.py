"""Route guard: never send people into another full zone.

Hillsborough's gate C let fans walk into pens that were already full. Every staff action Muse
Spark proposes that moves people (a redirect, an opened route) names its destination (`to`).
`check_routes` looks each destination up in the venue layout and reads what the cameras measure
there now: the density, its trend over the last minute and the fill clock. A destination that is
as dense as the zone people are leaving, or getting denser, is flagged, and the warden asks
Spark to choose again with those numbers in hand; one that no camera sees is marked unmeasured,
so the stewards know nobody has checked it. Spark makes the choice; this only measures and flags.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import Field

from rulecam.contracts import Frozen
from rulecam.crowd.forecast import ZoneForecast, forecast
from rulecam.crowd.scan import CrowdReading
from rulecam.crowd.venue import Venue

History = Callable[[str], Sequence[CrowdReading]]
"""Camera id -> its recent readings, oldest first."""


class RouteCheck(Frozen):
    """What the cameras say about one destination Spark sends people to."""

    action: str = Field(description="The action, as Spark wrote it.")
    to: str
    measured: bool
    camera_id: str | None = None
    zone: str | None = None
    density_ppm2: float | None = None
    per_min: float | None = None
    in_per_min: float | None = None
    source_density_ppm2: float | None = None
    filling: bool = Field(
        default=False,
        description="As dense as where people come from, or getting denser: Spark re-chooses.",
    )
    note: str = ""
    replaced: bool = Field(
        default=False, description="Spark chose again after this check; not acted on."
    )


def check_routes(
    moves: Sequence[tuple[str, str]],
    venue: Venue,
    history: History,
    *,
    source_density: float | None,
) -> list[RouteCheck]:
    """Each destination's measured crowd, flagged when it is filling.

    Args:
        moves: `(action text, destination name)` for every action that moves people.
        venue: The venue layout (places, with the camera and zone that sees each).
        history: Camera id -> its recent readings.
        source_density: Density of the zone people are moved away from (`None`: unknown).

    Returns:
        One check per move, in order.
    """
    out = []
    for action, to in moves:
        place = next(
            (p for p in venue.places if p.name.strip().lower() == to.strip().lower()), None
        )
        if place is None or place.camera_id is None:
            note = "not in the venue layout" if place is None else "no camera sees it"
            out.append(RouteCheck(action=action, to=to, measured=False, note=note))
            continue
        zone = place.zone or "view"
        found = _projection(history(place.camera_id), zone)
        if found is None or found.density_ppm2 is None:
            out.append(
                RouteCheck(
                    action=action,
                    to=to,
                    measured=False,
                    camera_id=place.camera_id,
                    zone=zone,
                    note="its camera has no density reading yet",
                )
            )
            continue
        denser = source_density is not None and found.density_ppm2 >= source_density
        rising = found.per_min is not None and found.per_min > 0 and found.points >= 3  # noqa: PLR2004
        out.append(
            RouteCheck(
                action=action,
                to=to,
                measured=True,
                camera_id=place.camera_id,
                zone=zone,
                density_ppm2=found.density_ppm2,
                per_min=found.per_min,
                in_per_min=found.in_per_min,
                source_density_ppm2=source_density,
                filling=denser or rising,
                note=_note(denser=denser, rising=rising),
            )
        )
    return out


def _projection(readings: Sequence[CrowdReading], zone: str) -> ZoneForecast | None:
    return next((f for f in forecast(readings) if f.zone == zone), None)


def _note(*, denser: bool, rising: bool) -> str:
    if denser and rising:
        return "as dense as where people come from, and getting denser"
    if denser:
        return "as dense as where people come from"
    if rising:
        return "getting denser"
    return "has room and is not filling"


def recheck_message(checks: Sequence[RouteCheck]) -> str:
    """The follow-up that asks Spark to choose again for the destinations that are filling."""
    lines = [
        f"- '{c.action}' sends people to {c.to}: {c.density_ppm2:g} people/m² now"
        + (f", {c.per_min:+.2f} per minute" if c.per_min is not None else "")
        + (f", {c.in_per_min:g} people/min crossing in" if c.in_per_min is not None else "")
        + f" ({c.note})."
        for c in checks
        if c.filling and c.density_ppm2 is not None
    ]
    return (
        "Route check from the cameras before anything goes out:\n"
        + "\n".join(lines)
        + "\nDo not send people into a zone that is filling. Choose again: keep a destination "
        "only if you can say why it is still safe, otherwise pick another place with room or "
        "hold the inflow instead. Reply with the whole JSON object again."
    )


__all__ = ["RouteCheck", "check_routes", "recheck_message"]
