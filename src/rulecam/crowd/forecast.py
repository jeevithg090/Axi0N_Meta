"""Where each zone's crowd is heading: the recent trend, inflow and time to reference densities.

Pure measurement, no decision. For each zone of a camera, over the last `RECENT_S` seconds of
readings:

- the density now and its change per minute (a least-squares line, `signals.trend`);
- the inflow: the change in people per minute in the zone (arrivals minus departures);
- the fill clock: people crossing the zone's edge in and out per minute (optical flow, the mean
  of the last `FLUX_READINGS` readings), and the seconds that net flow takes to bring the zone
  to each reference density at its area;
- for each published reference density (`REFERENCE_PPM2`), how many seconds the recent line
  takes to reach it (`None` when the line is flat or falling, or already past it), and how long
  the zone has stayed at or above it without a break.

The warden hands these to Muse Spark with the readings ("at this rate 6 per square metre in about
90 s"), and Spark decides what it means; the live page shows them next to Spark's own estimate.
The reference values are the background the warden's prompt already cites (G. Keith Still; the
UK Guide to Safety at Sports Grounds), not alarm thresholds.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from pydantic import Field

from rulecam.contracts import Frozen
from rulecam.crowd.scan import CrowdReading
from rulecam.crowd.signals import trend

RECENT_S: Final = 60.0  # the window the projection's line is fitted on
FLUX_READINGS: Final = 3  # the fill clock averages the edge crossings of this many readings
MAX_ETA_S: Final = 1800.0  # a projection further out than this says nothing useful
REFERENCE_PPM2: Final = (2.0, 4.0, 5.0, 6.0)
"""Published reference densities, people per m²: free flow ends, restricted, at risk, crushes."""


class ZoneForecast(Frozen):
    """One zone's recent trend and its projection to the reference densities."""

    zone: str
    density_ppm2: float | None = None
    per_min: float | None = Field(default=None, description="Density change per minute.")
    inflow_per_min: float | None = Field(
        default=None, description="People in the zone gained per minute (negative: emptying)."
    )
    people: int | None = None
    window_s: float = 0.0
    points: int = 0
    r2: float | None = None
    eta_s: dict[str, float | None] = Field(
        default_factory=dict,
        description="Reference density (as text) -> seconds until the recent line reaches it.",
    )
    above_s: dict[str, float] = Field(
        default_factory=dict,
        description="Reference density (as text) -> seconds the zone has stayed at or above it.",
    )
    peak_ppm2: float | None = None
    in_per_min: float | None = Field(
        default=None, description="People crossing the zone's edge inwards per minute (flow)."
    )
    out_per_min: float | None = Field(
        default=None, description="People crossing the zone's edge outwards per minute (flow)."
    )
    fill_eta_s: dict[str, float | None] = Field(
        default_factory=dict,
        description="Reference density -> seconds the net edge flow takes to reach it.",
    )


def forecast(history: Sequence[CrowdReading], recent_s: float = RECENT_S) -> list[ZoneForecast]:
    """Each zone's projection from a camera's readings (oldest first).

    Args:
        history: The camera's readings, oldest first.
        recent_s: Seconds of the newest readings the line is fitted on.

    Returns:
        One forecast per zone of the newest reading, in its order.
    """
    if not history:
        return []
    newest = history[-1]
    recent = [r for r in history if (newest.at - r.at).total_seconds() <= recent_s]
    return [_zone(zone.zone, history, recent) for zone in newest.zones]


def _zone(
    name: str, history: Sequence[CrowdReading], recent: Sequence[CrowdReading]
) -> ZoneForecast:
    start = recent[0].at
    density = [
        ((r.at - start).total_seconds(), z.density_ppm2)
        for r in recent
        if (z := r.zone(name)) is not None and z.density_ppm2 is not None
    ]
    people = [
        ((r.at - start).total_seconds(), float(z.people))
        for r in recent
        if (z := r.zone(name)) is not None
    ]
    now = recent[-1].zone(name)
    fit = trend([t for t, _ in density], [float(v) for _, v in density if v is not None])
    flow = trend([t for t, _ in people], [v for _, v in people])
    current = now.density_ppm2 if now is not None else None
    eta: dict[str, float | None] = {}
    for level in REFERENCE_PPM2:
        eta[_key(level)] = _eta(current, fit.per_min if fit else None, level)
    peaks = [v for _, v in density if v is not None]
    flux = _flux(name, recent)
    area = now.area_m2 if now is not None else None
    fill: dict[str, float | None] = {}
    if flux is not None and area:
        net_per_min = (flux[0] - flux[1]) / area
        fill = {_key(level): _eta(current, net_per_min, level) for level in REFERENCE_PPM2}
    return ZoneForecast(
        zone=name,
        density_ppm2=current,
        per_min=round(fit.per_min, 3) if fit is not None else None,
        inflow_per_min=round(flow.per_min, 2) if flow is not None else None,
        people=now.people if now is not None else None,
        window_s=round((recent[-1].at - start).total_seconds(), 1),
        points=len(density),
        r2=round(fit.r2, 3) if fit is not None else None,
        eta_s=eta,
        above_s={_key(level): _above(name, history, level) for level in REFERENCE_PPM2},
        peak_ppm2=max(peaks) if peaks else None,
        in_per_min=flux[0] if flux is not None else None,
        out_per_min=flux[1] if flux is not None else None,
        fill_eta_s=fill,
    )


def _flux(name: str, recent: Sequence[CrowdReading]) -> tuple[float, float] | None:
    """Mean edge crossings in and out per minute over the newest readings that measured them."""
    found = [
        (z.in_per_min, z.out_per_min)
        for r in recent
        if (z := r.zone(name)) is not None
        and z.in_per_min is not None
        and z.out_per_min is not None
    ][-FLUX_READINGS:]
    if not found:
        return None
    return (
        round(sum(i for i, _ in found if i is not None) / len(found), 1),
        round(sum(o for _, o in found if o is not None) / len(found), 1),
    )


def _eta(current: float | None, per_min: float | None, level: float) -> float | None:
    """Seconds until a line at `current` rising `per_min` reaches `level` (`None`: it does not)."""
    if current is None or per_min is None or current >= level or per_min <= 0:
        return None
    seconds = (level - current) / per_min * 60.0
    return round(seconds, 1) if seconds <= MAX_ETA_S else None


def _above(name: str, history: Sequence[CrowdReading], level: float) -> float:
    """Seconds the zone has stayed at or above `level`, counting back from the newest reading."""
    since = None
    for reading in reversed(history):
        zone = reading.zone(name)
        if zone is None or zone.density_ppm2 is None or zone.density_ppm2 < level:
            break
        since = reading.at
    if since is None:
        return 0.0
    return round((history[-1].at - since).total_seconds(), 1)


def _key(level: float) -> str:
    return f"{level:g}"


__all__ = ["RECENT_S", "REFERENCE_PPM2", "ZoneForecast", "forecast"]
