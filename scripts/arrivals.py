"""Seeded truck-arrival script and operator card for Site Advisor sessions (task P4-08a).

Usage:
    uv run python scripts/arrivals.py --session S2 --seed 7 --duration-min 25 \
        --cause replenishment --cause-start-min 13 --cause-duration-min 6
    uv run python scripts/arrivals.py --seed 7 --cause replenishment --print-cause

Arrivals are a Poisson process (exponential gaps) drawn from `random.Random(seed)`. The hidden
cause is a fixed window and never touches the random stream, so a cause session and a null session
with the same seed have identical arrivals. Minutes are "set minutes" on the toy warehouse set.
"""

from __future__ import annotations

import argparse
import random
from collections.abc import Sequence
from dataclasses import dataclass

DOCKS = ("dock_1", "dock_2", "dock_3")
CAUSE_NONE = "none"
CAUSE_REPLENISHMENT = "replenishment"
SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class Arrival:
    """One truck arrival on the set."""

    second: int
    dock: str


@dataclass(frozen=True)
class HiddenCause:
    """A planted cause: replenishment occupies both forklifts for a window."""

    start_min: float
    duration_min: float


def describe_cause(cause: HiddenCause) -> str:
    """Return the canonical cause text, the exact string that gets sealed.

    Args:
        cause: The planted cause.

    Returns:
        A one-line description that depends only on the cause window.
    """
    return (
        f"replenishment occupies both forklifts from set-minute {cause.start_min:g} "
        f"for {cause.duration_min:g} min"
    )


def generate_arrivals(
    seed: int, rate_per_hour: float, duration_min: float, docks: Sequence[str] = DOCKS
) -> tuple[Arrival, ...]:
    """Draw Poisson truck arrivals over a session.

    Args:
        seed: Seed for the random stream.
        rate_per_hour: Mean arrivals per set hour.
        duration_min: Session length in set minutes.
        docks: Docks to spread arrivals over.

    Returns:
        Arrivals in time order, all before `duration_min`.

    Raises:
        ValueError: If the rate or duration is not positive, or `docks` is empty.
    """
    if rate_per_hour <= 0 or duration_min <= 0:
        raise ValueError("rate_per_hour and duration_min must be positive")
    if not docks:
        raise ValueError("docks must not be empty")
    rng = random.Random(seed)  # noqa: S311 - seeded simulation, not cryptography
    rate_per_second = rate_per_hour / (SECONDS_PER_MINUTE * SECONDS_PER_MINUTE)
    horizon = duration_min * SECONDS_PER_MINUTE
    arrivals: list[Arrival] = []
    clock = rng.expovariate(rate_per_second)
    while clock < horizon:
        arrivals.append(Arrival(second=round(clock), dock=rng.choice(docks)))
        clock += rng.expovariate(rate_per_second)
    return tuple(arrivals)


def validate_cause(cause: HiddenCause, duration_min: float) -> None:
    """Check that the cause window fits inside the session.

    Args:
        cause: The planted cause.
        duration_min: Session length in set minutes.

    Raises:
        ValueError: If the window starts before 0, is empty, or ends after the session.
    """
    if cause.start_min < 0 or cause.duration_min <= 0:
        raise ValueError("cause window must start at or after 0 and last longer than 0")
    if cause.start_min + cause.duration_min > duration_min:
        raise ValueError("cause window must end before the session does")


def format_clock(second: int) -> str:
    """Format elapsed seconds as `mm:ss`."""
    minutes, seconds = divmod(second, SECONDS_PER_MINUTE)
    return f"{minutes:02d}:{seconds:02d}"


def render_card(
    session: str,
    seed: int,
    duration_min: float,
    arrivals: Sequence[Arrival],
    cause: HiddenCause | None,
) -> str:
    """Render the operator schedule card for one session.

    Args:
        session: Session label, such as `S2`.
        seed: Seed the arrivals were drawn from.
        duration_min: Session length in set minutes.
        arrivals: Arrivals from `generate_arrivals`.
        cause: The planted cause, or None for a null session.

    Returns:
        The card as text: a header, then events in time order.
    """
    events = [(a.second, f"TRUCK arrives at {a.dock}") for a in arrivals]
    if cause is not None:
        start = round(cause.start_min * SECONDS_PER_MINUTE)
        end = round((cause.start_min + cause.duration_min) * SECONDS_PER_MINUTE)
        events.append((start, "REPLENISHMENT starts: both forklifts busy on the racks"))
        events.append((end, "REPLENISHMENT ends: forklifts free"))
    header = [
        f"Session {session} | seed {seed} | {duration_min:g} set minutes",
        f"Planted cause: {'on' if cause is not None else CAUSE_NONE}",
        "Times are set minutes (mm:ss from session start), not real-world time.",
        "",
    ]
    body = [f"{format_clock(second)}  {text}" for second, text in sorted(events)]
    return "\n".join([*header, *body, "", f"{format_clock(round(duration_min * 60))}  END"])


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--session", default="S1", help="session label printed on the card")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--rate-per-hour", type=float, default=30.0)
    parser.add_argument("--duration-min", type=float, default=25.0)
    parser.add_argument("--cause", choices=(CAUSE_NONE, CAUSE_REPLENISHMENT), default=CAUSE_NONE)
    parser.add_argument("--cause-start-min", type=float, default=13.0)
    parser.add_argument("--cause-duration-min", type=float, default=6.0)
    parser.add_argument(
        "--print-cause",
        action="store_true",
        help="print only the canonical cause text (the string to seal) and exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI: print the operator card, or the cause text with `--print-cause`."""
    args = build_parser().parse_args(argv)
    cause = (
        HiddenCause(args.cause_start_min, args.cause_duration_min)
        if args.cause == CAUSE_REPLENISHMENT
        else None
    )
    if cause is not None:
        validate_cause(cause, args.duration_min)
    if args.print_cause:
        if cause is None:
            raise SystemExit("--print-cause needs --cause replenishment")
        print(describe_cause(cause))
        return 0
    arrivals = generate_arrivals(args.seed, args.rate_per_hour, args.duration_min)
    print(render_card(args.session, args.seed, args.duration_min, arrivals, cause))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
