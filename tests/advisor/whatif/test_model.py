from __future__ import annotations

from rulecam.advisor.whatif import QueueObservations, Scenario, WhatIf, queue_what_if
from tests.advisor.dock_log import simulate_queue


def run(data: QueueObservations, scenario: Scenario, seed: int = 1) -> WhatIf | None:
    return queue_what_if(data, scenario, seed=seed, operating_hours_per_week=40.0)


def observed(
    seed: int = 1, mean_gap: float = 120.0, mean_service: float = 80.0, n: int = 150
) -> QueueObservations:
    visits = simulate_queue(seed, n, mean_gap, mean_service)
    return QueueObservations(
        arrival_times_s=[v.arrive_s for v in visits],
        service_times_s=[v.handling_s for v in visits],
        observed_waits_s=[v.wait_s for v in visits],
        window_s=visits[-1].arrive_s + 10.0,
        servers=1,
        bin_s=300.0,
    )


def test_an_extra_server_shortens_the_wait() -> None:
    result = run(observed(), Scenario(servers_added=1))
    assert result is not None
    low, high = result.gain.interval
    assert low < high < 0  # the change in mean wait per truck is negative
    assert result.gain.unit == "min/truck"
    assert result.gain.level == 0.8
    assert result.gain.model_rung == "erlang_c"
    assert result.p20_weekly_value > 0
    assert result.gain.assumptions["baseline_fit"] == "pass"


def test_the_result_is_reproducible_and_depends_on_the_seed() -> None:
    first = run(observed(), Scenario(servers_added=1))
    again = run(observed(), Scenario(servers_added=1))
    other = run(observed(), Scenario(servers_added=1), seed=2)
    assert first == again
    assert first is not None
    assert other is not None
    assert other.gain.interval != first.gain.interval


def test_smoothing_arrivals_is_modelled_and_shortens_the_wait() -> None:
    # Under the Erlang C rung the arrivals are Poisson (variation 1); halving that variation is
    # an Allen-Cunneen what-if. It must not come back as a made-up gain of exactly zero.
    result = run(observed(), Scenario(arrival_scv_scale=0.5))
    assert result is not None
    assert result.gain.model_rung == "erlang_c"
    low, high = result.gain.interval
    assert low < high < 0
    assert result.p20_weekly_value > 0


def test_smoothing_helps_less_than_adding_a_server_here() -> None:
    smooth = run(observed(), Scenario(arrival_scv_scale=0.5))
    server = run(observed(), Scenario(servers_added=1))
    assert smooth is not None
    assert server is not None
    assert server.gain.interval[1] < smooth.gain.interval[1]


def test_a_scenario_that_changes_nothing_has_no_gain() -> None:
    result = run(observed(), Scenario())
    assert result is not None
    assert result.gain.interval == (0.0, 0.0)  # genuinely nothing changed, and said so


def test_none_when_the_station_is_overloaded() -> None:
    assert run(observed(mean_gap=80.0, mean_service=100.0), Scenario(servers_added=1)) is None


def test_none_when_the_model_does_not_reproduce_the_observed_wait() -> None:
    data = observed()
    skewed = QueueObservations(
        arrival_times_s=data.arrival_times_s,
        service_times_s=data.service_times_s,
        observed_waits_s=[w + 3000.0 for w in data.observed_waits_s],
        window_s=data.window_s,
        servers=1,
        bin_s=300.0,
    )
    assert run(skewed, Scenario(servers_added=1)) is None


def test_none_with_too_little_data() -> None:
    assert run(observed(n=4), Scenario(servers_added=1)) is None
