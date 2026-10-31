"""What-if gain for a change that adds capacity or smooths arrivals (task P4-08b).

The station is modelled as a G/G/c queue for the resource trucks wait on (forklifts). The gain
interval comes from bootstrapping the observed gaps between arrivals and service times, so it
reflects how little data there is. Nothing here is a business saving: it is time freed per truck.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from rulecam.advisor.whatif.assumptions import Rung, check_assumptions, choose_rung, squared_cv
from rulecam.advisor.whatif.queueing import (
    UnstableQueueError,
    allen_cunneen_wait,
    mmc_wait,
)
from rulecam.contracts.advisor import Gain

SECONDS_PER_MINUTE = 60.0
MIN_GAPS = 5
MAX_UNSTABLE_SHARE = 0.2
FIT_TOLERANCE = 0.5
FIT_FLOOR_MIN = 0.5


@dataclass(frozen=True)
class QueueObservations:
    """What was measured at a station over a window."""

    arrival_times_s: Sequence[float]
    service_times_s: Sequence[float]
    observed_waits_s: Sequence[float]
    window_s: float
    servers: int
    bin_s: float


@dataclass(frozen=True)
class Scenario:
    """A change to model: extra servers and/or a scaling of arrival variability."""

    servers_added: int = 0
    arrival_scv_scale: float = 1.0


@dataclass(frozen=True)
class WhatIf:
    """A modelled gain and its pessimistic weekly value (minutes of waiting freed per week)."""

    gain: Gain
    p20_weekly_value: float


def _gaps(arrival_times_s: Sequence[float]) -> np.ndarray:
    return np.diff(np.sort(np.asarray(arrival_times_s, dtype=np.float64)))


def _wait(
    rung: Rung, servers: int, gaps: np.ndarray, services: np.ndarray, arrival_scv_scale: float
) -> float | None:
    """Modelled mean wait in seconds, or None if the system is unstable."""
    arrival_rate = 1.0 / float(gaps.mean())
    service_rate = 1.0 / float(services.mean())
    try:
        if rung == "erlang_c" and arrival_scv_scale == 1.0:
            return mmc_wait(arrival_rate, service_rate, servers)
        if rung == "erlang_c":
            # The Erlang C checks passed, so arrivals and service are taken as exponential
            # (variation 1). Smoothing arrivals scales that variation; Erlang C alone can't say.
            return allen_cunneen_wait(arrival_rate, service_rate, servers, arrival_scv_scale, 1.0)
        arrival_scv = squared_cv(gaps.tolist())
        service_scv = squared_cv(services.tolist())
        if arrival_scv is None or service_scv is None:
            return None
        return allen_cunneen_wait(
            arrival_rate, service_rate, servers, arrival_scv * arrival_scv_scale, service_scv
        )
    except UnstableQueueError:
        return None


def _counts(arrival_times_s: Sequence[float], window_s: float, bin_s: float) -> list[int]:
    edges = np.arange(0.0, window_s + bin_s, bin_s)
    counts, _ = np.histogram(arrival_times_s, bins=edges)
    return [int(c) for c in counts[: max(1, int(window_s // bin_s))]]


def queue_what_if(
    observed: QueueObservations,
    scenario: Scenario,
    *,
    seed: int,
    operating_hours_per_week: float,
    metric: str = "wait_for_forklift",
    level: float = 0.8,
    n_resamples: int = 1000,
) -> WhatIf | None:
    """Model the change in mean wait per truck for a scenario.

    Args:
        observed: Arrival times (seconds from the window start), service times and waits.
        scenario: Extra servers and/or smoother arrivals.
        seed: Seed for the bootstrap.
        operating_hours_per_week: Hours the station runs in a week, to scale to a weekly value.
        metric: The metric the gain applies to.
        level: Interval level for the gain.
        n_resamples: Bootstrap resamples.

    Returns:
        The gain, or None when no rung's assumptions hold, the model does not reproduce the
        observed wait, or too many resamples are unstable.
    """
    gaps = _gaps(observed.arrival_times_s)
    services = np.asarray(observed.service_times_s, dtype=np.float64)
    if gaps.size < MIN_GAPS or services.size < MIN_GAPS or not observed.observed_waits_s:
        return None
    utilisation = float(services.mean() / gaps.mean()) / observed.servers
    checks = check_assumptions(
        _counts(observed.arrival_times_s, observed.window_s, observed.bin_s),
        services.tolist(),
        utilisation,
    )
    rung = choose_rung(checks)
    if rung is None:
        return None
    base = _wait(rung, observed.servers, gaps, services, 1.0)
    if base is None:
        return None
    seen = float(np.mean(observed.observed_waits_s))
    error = abs(base - seen) / SECONDS_PER_MINUTE
    fits = error <= max(FIT_FLOOR_MIN, FIT_TOLERANCE * seen / SECONDS_PER_MINUTE)
    checks["baseline_fit"] = "pass" if fits else "fail"
    if not fits:
        return None

    rng = np.random.default_rng(seed)
    changes: list[float] = []
    for _ in range(n_resamples):
        g = rng.choice(gaps, size=gaps.size)
        s = rng.choice(services, size=services.size)
        before = _wait(rung, observed.servers, g, s, 1.0)
        after = _wait(
            rung, observed.servers + scenario.servers_added, g, s, scenario.arrival_scv_scale
        )
        if before is not None and after is not None:
            changes.append((after - before) / SECONDS_PER_MINUTE)
    if len(changes) < (1.0 - MAX_UNSTABLE_SHARE) * n_resamples:
        return None
    tail = (1.0 - level) / 2.0
    lower, upper = np.quantile(changes, [tail, 1.0 - tail])
    pessimistic_saving = -float(np.quantile(changes, 1.0 - 0.2))  # 20th percentile of the saving
    trucks_per_hour = len(observed.arrival_times_s) / observed.window_s * 3600.0
    trucks_per_week = trucks_per_hour * operating_hours_per_week
    gain = Gain(
        metric=metric,
        interval=(float(lower), float(upper)),
        level=level,
        unit="min/truck",
        model_rung=rung,
        assumptions=checks,
    )
    weekly = pessimistic_saving * trucks_per_week
    return WhatIf(gain=gain, p20_weekly_value=0.0 if math.isnan(weekly) else weekly)
