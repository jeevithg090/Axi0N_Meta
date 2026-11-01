"""Queueing formulas for the what-if ladder (task P4-08b): Little, Erlang C, Allen-Cunneen.

Rates are per second and times are in seconds, but every function works in any consistent unit.
"""

from __future__ import annotations


class UnstableQueueError(ValueError):
    """The offered load meets or exceeds capacity, so the queue has no steady state."""


def littles_law_wip(arrival_rate: float, time_in_system: float) -> float:
    """Average number in the system: `L = lambda * W`."""
    if arrival_rate < 0 or time_in_system < 0:
        raise ValueError("arrival_rate and time_in_system must not be negative")
    return arrival_rate * time_in_system


def littles_law_time(wip: float, arrival_rate: float) -> float:
    """Average time in the system: `W = L / lambda`.

    Raises:
        ValueError: If `arrival_rate` is not positive or `wip` is negative.
    """
    if arrival_rate <= 0 or wip < 0:
        raise ValueError("arrival_rate must be positive and wip must not be negative")
    return wip / arrival_rate


def erlang_c(servers: int, offered_load: float) -> float:
    """Probability that an arrival has to wait in an M/M/c queue.

    Args:
        servers: Number of servers `c`, at least 1.
        offered_load: `a = lambda / mu`, in erlangs.

    Returns:
        The probability of waiting.

    Raises:
        ValueError: If `servers` is below 1 or `offered_load` is negative.
        UnstableQueueError: If `offered_load >= servers`.
    """
    if servers < 1 or offered_load < 0:
        raise ValueError("servers must be at least 1 and offered_load must not be negative")
    if offered_load >= servers:
        raise UnstableQueueError(f"offered load {offered_load:g} needs more than {servers} servers")
    blocking = 1.0
    for k in range(1, servers + 1):  # Erlang B by its stable recursion
        blocking = offered_load * blocking / (k + offered_load * blocking)
    utilisation = offered_load / servers
    return blocking / (1.0 - utilisation * (1.0 - blocking))


def mmc_wait(arrival_rate: float, service_rate: float, servers: int) -> float:
    """Mean wait in the queue for an M/M/c system: `C(c, a) / (c * mu - lambda)`.

    Raises:
        ValueError: If a rate is not positive.
        UnstableQueueError: If the system is overloaded.
    """
    if arrival_rate <= 0 or service_rate <= 0:
        raise ValueError("arrival_rate and service_rate must be positive")
    probability_wait = erlang_c(servers, arrival_rate / service_rate)
    return probability_wait / (servers * service_rate - arrival_rate)


def allen_cunneen_wait(
    arrival_rate: float,
    service_rate: float,
    servers: int,
    arrival_scv: float,
    service_scv: float,
) -> float:
    """Mean wait in the queue for a G/G/c system (Allen-Cunneen approximation).

    It scales the M/M/c wait by `(ca2 + cs2) / 2`, where `ca2` and `cs2` are the squared
    coefficients of variation of the gaps between arrivals and of service times. With both equal
    to 1 it is exactly the Erlang C result.

    Raises:
        ValueError: If a rate is not positive or a variation is negative.
        UnstableQueueError: If the system is overloaded.
    """
    if arrival_scv < 0 or service_scv < 0:
        raise ValueError("squared coefficients of variation must not be negative")
    return mmc_wait(arrival_rate, service_rate, servers) * (arrival_scv + service_scv) / 2.0
