from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "arrivals.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arrivals", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


arrivals = load_script()


def test_same_seed_reproduces_and_other_seed_differs() -> None:
    first = arrivals.generate_arrivals(7, 30.0, 25.0)
    assert first == arrivals.generate_arrivals(7, 30.0, 25.0)
    assert first != arrivals.generate_arrivals(8, 30.0, 25.0)


def test_arrivals_are_ordered_inside_the_session_on_known_docks() -> None:
    result = arrivals.generate_arrivals(3, 60.0, 20.0)
    seconds = [a.second for a in result]
    assert seconds == sorted(seconds)
    assert all(0 <= s < 20 * 60 for s in seconds)
    assert {a.dock for a in result} <= set(arrivals.DOCKS)


def test_mean_count_matches_the_rate() -> None:
    # 120/h over 10 set hours: expect 1200, sd about 35, so 5 sd is about 175.
    count = len(arrivals.generate_arrivals(11, 120.0, 600.0))
    assert abs(count - 1200) < 175


@pytest.mark.parametrize(
    ("rate", "duration", "docks"),
    [(0.0, 25.0, ("dock_1",)), (30.0, 0.0, ("dock_1",)), (30.0, 25.0, ())],
)
def test_invalid_inputs_are_rejected(rate: float, duration: float, docks: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="must"):
        arrivals.generate_arrivals(1, rate, duration, docks)


def test_cause_window_must_fit_in_the_session() -> None:
    arrivals.validate_cause(arrivals.HiddenCause(13.0, 6.0), 25.0)
    for bad in (
        arrivals.HiddenCause(-1.0, 6.0),
        arrivals.HiddenCause(13.0, 0.0),
        arrivals.HiddenCause(22.0, 6.0),
    ):
        with pytest.raises(ValueError, match="cause window"):
            arrivals.validate_cause(bad, 25.0)


def test_cause_text_depends_only_on_the_window() -> None:
    cause = arrivals.HiddenCause(13.0, 6.0)
    assert arrivals.describe_cause(cause) == (
        "replenishment occupies both forklifts from set-minute 13 for 6 min"
    )


def test_cause_session_and_null_session_share_the_same_truck_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    arrivals.main(["--seed", "5", "--cause", "replenishment"])
    with_cause = capsys.readouterr().out
    arrivals.main(["--seed", "5"])
    without = capsys.readouterr().out
    trucks = [line for line in with_cause.splitlines() if "TRUCK" in line]
    assert trucks
    assert trucks == [line for line in without.splitlines() if "TRUCK" in line]


def test_card_lists_events_in_time_order_with_the_cause() -> None:
    cause = arrivals.HiddenCause(13.0, 6.0)
    trucks = arrivals.generate_arrivals(7, 30.0, 25.0)
    card = arrivals.render_card("S2", 7, 25.0, trucks, cause)
    lines = card.splitlines()
    assert lines[0] == "Session S2 | seed 7 | 25 set minutes"
    assert lines[1] == "Planted cause: on"
    events = [line for line in lines[4:] if line]
    stamps = [line.split()[0] for line in events]
    assert stamps == sorted(stamps)
    assert any("13:00  REPLENISHMENT starts" in line for line in events)
    assert any("19:00  REPLENISHMENT ends" in line for line in events)
    assert events[-1] == "25:00  END"


def test_null_card_has_no_replenishment_events() -> None:
    trucks = arrivals.generate_arrivals(7, 30.0, 25.0)
    card = arrivals.render_card("S3", 7, 25.0, trucks, None)
    assert "Planted cause: none" in card
    assert "REPLENISHMENT" not in card


def test_format_clock() -> None:
    assert arrivals.format_clock(0) == "00:00"
    assert arrivals.format_clock(754) == "12:34"


def test_cli_prints_card_and_cause_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert arrivals.main(["--session", "S2", "--seed", "7", "--cause", "replenishment"]) == 0
    assert "Planted cause: on" in capsys.readouterr().out
    assert arrivals.main(["--seed", "7", "--cause", "replenishment", "--print-cause"]) == 0
    assert capsys.readouterr().out.strip() == (
        "replenishment occupies both forklifts from set-minute 13 for 6 min"
    )


def test_cli_rejects_print_cause_without_a_cause() -> None:
    with pytest.raises(SystemExit, match="--print-cause needs"):
        arrivals.main(["--seed", "7", "--print-cause"])


def test_cli_rejects_a_cause_window_past_the_end() -> None:
    with pytest.raises(ValueError, match="cause window"):
        arrivals.main(["--seed", "7", "--cause", "replenishment", "--cause-start-min", "24"])


def test_print_cause_refuses_a_window_the_card_would_reject() -> None:
    # The sealed text must describe a session that can actually run.
    with pytest.raises(ValueError, match="cause window"):
        arrivals.main(
            ["--seed", "7", "--cause", "replenishment", "--cause-start-min", "24", "--print-cause"]
        )
