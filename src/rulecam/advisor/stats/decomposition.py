"""Mix/rate decomposition of a gap between two periods (task P4-08b)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Stratum:
    """One stratum (for example an hour of the day) in both periods.

    `share_*` is the stratum's share of cases and `rate_*` its mean outcome.
    """

    name: str
    share_a: float
    rate_a: float
    share_b: float
    rate_b: float


@dataclass(frozen=True)
class Decomposition:
    """The gap `b - a` split into a rate part and a mix part, which sum to `total_gap`."""

    total_gap: float
    rate: float
    mix: float
    by_stratum: Mapping[str, float]


def mix_rate_decomposition(strata: Sequence[Stratum]) -> Decomposition:
    """Split the change in an overall mean into a rate effect and a mix effect.

    The rate effect is the change in each stratum's mean at the average mix. The mix effect is the
    change in the mix at the average rates. The two sum to the total gap exactly.

    Args:
        strata: The strata, with shares and rates in both periods.

    Returns:
        The total gap, its two parts and each stratum's contribution.
    """
    rate = mix = 0.0
    by_stratum: dict[str, float] = {}
    for s in strata:
        stratum_rate = (s.share_a + s.share_b) / 2.0 * (s.rate_b - s.rate_a)
        stratum_mix = (s.rate_a + s.rate_b) / 2.0 * (s.share_b - s.share_a)
        rate += stratum_rate
        mix += stratum_mix
        by_stratum[s.name] = stratum_rate + stratum_mix
    total = sum(s.share_b * s.rate_b - s.share_a * s.rate_a for s in strata)
    return Decomposition(total_gap=total, rate=rate, mix=mix, by_stratum=by_stratum)


def strata_from_samples(
    period_a: Mapping[str, Sequence[float]], period_b: Mapping[str, Sequence[float]]
) -> tuple[Stratum, ...]:
    """Build strata from the raw outcomes of two periods, keyed by stratum name.

    A stratum missing from one period gets share 0 and rate 0 there.

    Raises:
        ValueError: If either period has no observations at all.
    """
    total_a = sum(len(v) for v in period_a.values())
    total_b = sum(len(v) for v in period_b.values())
    if total_a == 0 or total_b == 0:
        raise ValueError("both periods need at least one observation")
    names = sorted(set(period_a) | set(period_b))
    strata = []
    for name in names:
        a, b = period_a.get(name, ()), period_b.get(name, ())
        strata.append(
            Stratum(
                name=name,
                share_a=len(a) / total_a,
                rate_a=sum(a) / len(a) if a else 0.0,
                share_b=len(b) / total_b,
                rate_b=sum(b) / len(b) if b else 0.0,
            )
        )
    return tuple(strata)
