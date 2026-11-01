from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "simulate_bottleneck_detection.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("simulate_bottleneck_detection", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


sim = load_script()


def test_the_same_arguments_give_the_same_table() -> None:
    assert sim.format_table(sim.simulate(20), 20) == sim.format_table(sim.simulate(20), 20)


def test_a_higher_floor_never_raises_the_false_flag_rate_or_the_power() -> None:
    results = sim.simulate(40)
    assert [r.floor_s for r in results] == list(sim.FLOORS_S)
    for lower, higher in itertools.pairwise(results):
        assert higher.false_flag_rate <= lower.false_flag_rate
        assert higher.power <= lower.power


def test_a_busy_window_is_flagged_more_often_than_a_quiet_one() -> None:
    no_floor = sim.simulate(40)[0]
    assert no_floor.power > no_floor.false_flag_rate


def test_waits_are_a_seeded_fifo_queue() -> None:
    first = sim.waits(1, 50, 130.0)
    assert first == sim.waits(1, 50, 130.0)
    assert first != sim.waits(2, 50, 130.0)
    assert first[0] == 0.0  # the first visit finds the queue empty
    assert all(w >= 0 for w in first)


def test_main_prints_the_table(capsys: object) -> None:
    assert sim.main(["--runs", "10"]) == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert out.startswith("10 runs, alpha 0.05")
    assert "floor    false flags    power" in out
