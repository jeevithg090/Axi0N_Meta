from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulecam.advisor.whatif import (
    UnstableQueueError,
    allen_cunneen_wait,
    erlang_c,
    littles_law_time,
    littles_law_wip,
    mmc_wait,
)


def erlang_c_direct(servers: int, load: float) -> float:
    """Erlang C from its textbook sum, as an independent check on the recursion."""
    top = load**servers / math.factorial(servers) * servers / (servers - load)
    return top / (sum(load**k / math.factorial(k) for k in range(servers)) + top)


def test_erlang_c_matches_closed_forms() -> None:
    assert erlang_c(1, 0.5) == pytest.approx(0.5)  # M/M/1: P(wait) = rho
    assert erlang_c(2, 1.0) == pytest.approx(1 / 3)  # c=2: 2 rho^2 / (1 + rho), rho = 0.5
    assert erlang_c(1, 0.0) == 0.0


@given(st.integers(min_value=1, max_value=30), st.floats(min_value=0.01, max_value=0.99))
def test_erlang_c_matches_the_textbook_sum(servers: int, utilisation: float) -> None:
    load = servers * utilisation
    assert erlang_c(servers, load) == pytest.approx(erlang_c_direct(servers, load), rel=1e-6)


def test_more_servers_never_increase_the_chance_of_waiting() -> None:
    assert erlang_c(3, 2.0) > erlang_c(4, 2.0) > erlang_c(5, 2.0)


def test_unstable_and_invalid_inputs() -> None:
    with pytest.raises(UnstableQueueError):
        erlang_c(2, 2.0)
    with pytest.raises(ValueError, match="servers"):
        erlang_c(0, 0.5)
    with pytest.raises(ValueError, match="positive"):
        mmc_wait(0.0, 1.0, 1)


def test_mm1_wait_matches_the_formula() -> None:
    assert mmc_wait(0.5, 1.0, 1) == pytest.approx(1.0)  # rho / (mu (1 - rho))
    assert mmc_wait(0.8, 1.0, 1) == pytest.approx(4.0)


def test_allen_cunneen_reduces_to_erlang_c_when_both_variations_are_one() -> None:
    for servers, arrival, service in [(1, 0.5, 1.0), (3, 2.0, 1.0), (4, 3.2, 1.0)]:
        assert allen_cunneen_wait(arrival, service, servers, 1.0, 1.0) == pytest.approx(
            mmc_wait(arrival, service, servers)
        )


def test_allen_cunneen_scales_with_variation() -> None:
    base = mmc_wait(0.5, 1.0, 1)
    assert allen_cunneen_wait(0.5, 1.0, 1, 0.0, 1.0) == pytest.approx(base / 2)
    assert allen_cunneen_wait(0.5, 1.0, 1, 2.0, 2.0) == pytest.approx(base * 2)
    with pytest.raises(ValueError, match="variation"):
        allen_cunneen_wait(0.5, 1.0, 1, -1.0, 1.0)


def test_littles_law() -> None:
    assert littles_law_wip(2.0, 5.0) == 10.0
    assert littles_law_time(10.0, 2.0) == 5.0
    with pytest.raises(ValueError, match="arrival_rate"):
        littles_law_time(1.0, 0.0)
    with pytest.raises(ValueError, match="negative"):
        littles_law_wip(-1.0, 1.0)
