"""Reproduce the bottleneck-detection figures quoted in `src/rulecam/advisor/README.md` (P4-08b).

Usage:
    uv run python scripts/simulate_bottleneck_detection.py            # 300 runs, as quoted
    uv run python scripts/simulate_bottleneck_detection.py --runs 50

Each run draws a quiet baseline of 150 visits, then a 30-visit window from either the same quiet
process (to count false flags) or a busier one (to count detections). All queues are FIFO
single-server M/M/1 with a mean service time of 80 s: the baseline and the "unchanged" window
arrive every 400 s on average (utilisation 0.2) and the "slower" window every 130 s (about 0.6).
A window is flagged when the moving-window p-value is below 0.05 and its mean wait is at least
`floor` seconds above the baseline's. This is one station at the nominal 5% level, with no
Benjamini-Hochberg step, so it isolates the test and the floor.

Seeds are fixed (7000 + i, 9000 + i, 11000 + i), so the same arguments print the same table.
"""

from __future__ import annotations

import argparse
import random
from collections.abc import Sequence
from dataclasses import dataclass

from rulecam.advisor.stats import moving_window_pvalue

BASELINE_VISITS = 150
WINDOW_VISITS = 30
QUIET_GAP_S = 400.0
BUSY_GAP_S = 130.0
SERVICE_S = 80.0
ALPHA = 0.05
FLOORS_S = (0, 30, 60, 120)
BASELINE_SEED, QUIET_SEED, BUSY_SEED = 7000, 9000, 11000


@dataclass(frozen=True)
class FloorResult:
    """Flag rates for one effect-size floor."""

    floor_s: int
    false_flag_rate: float
    power: float


def waits(seed: int, visits: int, mean_gap_s: float) -> list[float]:
    """Waits of a FIFO single-server queue with exponential gaps and service times."""
    rng = random.Random(seed)  # noqa: S311 - seeded simulation, not cryptography
    clock = free_at = 0.0
    out = []
    for _ in range(visits):
        clock += rng.expovariate(1.0 / mean_gap_s)
        service = rng.expovariate(1.0 / SERVICE_S)
        begin = max(clock, free_at)
        free_at = begin + service
        out.append(begin - clock)
    return out


def _flagged(window: Sequence[float], baseline: Sequence[float], floor_s: int) -> bool:
    p = moving_window_pvalue(window, [baseline])
    effect = sum(window) / len(window) - sum(baseline) / len(baseline)
    return p is not None and p < ALPHA and effect >= floor_s


def simulate(runs: int) -> list[FloorResult]:
    """Flag rates, over `runs` seeded runs, for each floor in `FLOORS_S`."""
    cases = []
    for i in range(runs):
        baseline = waits(BASELINE_SEED + i, BASELINE_VISITS, QUIET_GAP_S)
        quiet = waits(QUIET_SEED + i, WINDOW_VISITS, QUIET_GAP_S)
        busy = waits(BUSY_SEED + i, WINDOW_VISITS, BUSY_GAP_S)
        cases.append((baseline, quiet, busy))
    return [
        FloorResult(
            floor,
            sum(_flagged(q, b, floor) for b, q, _ in cases) / runs,
            sum(_flagged(s, b, floor) for b, _, s in cases) / runs,
        )
        for floor in FLOORS_S
    ]


def format_table(results: Sequence[FloorResult], runs: int) -> str:
    """The results as a text table."""
    lines = [f"{runs} runs, alpha {ALPHA}", "floor    false flags    power"]
    lines += [f"{r.floor_s:>4} s  {r.false_flag_rate:>10.1%}  {r.power:>8.1%}" for r in results]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the simulation and print the table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--runs", type=int, default=300)
    args = parser.parse_args(argv)
    print(format_table(simulate(args.runs), args.runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
