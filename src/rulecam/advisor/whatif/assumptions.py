"""Assumption checks that pick the what-if rung (task P4-08b).

The thresholds are heuristics, tuned on the staged sessions; the assumption card shows each check
and its verdict so a reader can see why a rung was or was not used.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Final, Literal

Verdict = Literal["pass", "fail", "n/a"]
Rung = Literal["erlang_c", "allen_cunneen"]

DISPERSION_RANGE: Final = (0.5, 1.5)
SERVICE_SCV_RANGE: Final = (0.6, 1.4)
MIN_BINS = 4
MIN_FOR_VARIANCE = 2
MIN_SERVICES = 5
MAX_UTILISATION = 0.95


def dispersion_index(counts: Sequence[int]) -> float | None:
    """Variance over mean of arrival counts per interval; about 1 for Poisson arrivals.

    Returns:
        The index, or None with too few intervals or no arrivals.
    """
    if len(counts) < MIN_BINS:
        return None
    mean = statistics.fmean(counts)
    if mean == 0:
        return None
    return statistics.variance(counts) / mean


def squared_cv(values: Sequence[float]) -> float | None:
    """Squared coefficient of variation (variance over mean squared), or None if undefined."""
    if len(values) < MIN_FOR_VARIANCE:
        return None
    mean = statistics.fmean(values)
    if mean <= 0:
        return None
    return statistics.variance(values) / mean**2


def _within(value: float | None, bounds: tuple[float, float]) -> Verdict:
    if value is None:
        return "n/a"
    return "pass" if bounds[0] <= value <= bounds[1] else "fail"


def check_assumptions(
    arrival_counts: Sequence[int], service_times: Sequence[float], utilisation: float | None
) -> dict[str, Verdict]:
    """Run the three checks behind the rung choice.

    Args:
        arrival_counts: Arrivals in each equal interval of the window.
        service_times: Observed service durations.
        utilisation: Offered load per server, or None if it cannot be computed.

    Returns:
        Verdicts for `poisson_arrivals`, `exponential_service` and `stable`.
    """
    service_scv = squared_cv(service_times) if len(service_times) >= MIN_SERVICES else None
    stable: Verdict = (
        "n/a" if utilisation is None else "pass" if utilisation < MAX_UTILISATION else "fail"
    )
    return {
        "poisson_arrivals": _within(dispersion_index(arrival_counts), DISPERSION_RANGE),
        "exponential_service": _within(service_scv, SERVICE_SCV_RANGE),
        "stable": stable,
    }


def choose_rung(checks: dict[str, Verdict]) -> Rung | None:
    """Pick the simplest rung whose assumptions hold.

    Erlang C needs Poisson arrivals and exponential service; Allen-Cunneen drops both but needs a
    stable system and measured variation. Without a stable system, no queueing rung applies.
    """
    if checks["stable"] != "pass":
        return None
    if checks["poisson_arrivals"] == "pass" and checks["exponential_service"] == "pass":
        return "erlang_c"
    if "n/a" in (checks["poisson_arrivals"], checks["exponential_service"]):
        return None
    return "allen_cunneen"
