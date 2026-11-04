"""Seeded, clearly labelled SIMULATED store observations, for the Chain Mode demo and tests.

Real stores report `RuleOutcome`s and coverage segments from their runtime. Until a chain of
stores is live, `simulate` makes the same shapes from a small profile per store (how often each
rule is met, when a camera was BLIND and why), so the HQ scorecard can be shown end to end.
Every observation it makes carries `Provenance.SIMULATED`; the scorecard and the report print
that label, and no business figure is ever drawn from it (SA §9).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import numpy as np
import yaml
from pydantic import Field, ValidationError

from rulecam.chain.errors import ObservationsError
from rulecam.chain.models import ChainObservations, StoreFit, StoreObservations
from rulecam.contracts.core import Frozen, Provenance
from rulecam.perception.coverage import CoverageReason, CoverageSegment, RuleOutcome

SEGMENT: Final = timedelta(minutes=5)
OUTCOME_SPAN: Final = timedelta(seconds=30)


class BlindSpan(Frozen):
    """A stretch of time a camera was BLIND, in hours from the window start."""

    from_h: float = Field(ge=0)
    hours: float = Field(gt=0)
    reason: CoverageReason


class StoreProfile(Frozen):
    """How one simulated store behaves.

    Attributes:
        compliance: Rule id -> the chance one opportunity is met.
        blind: Camera id -> when it was BLIND.
    """

    compliance: dict[str, float] = Field(default_factory=dict)
    blind: dict[str, tuple[BlindSpan, ...]] = Field(default_factory=dict)


class Simulation(Frozen):
    """A seeded simulation of every store over one window.

    Attributes:
        seed: The random seed; the same seed gives the same observations.
        start: Window start.
        hours: Window length.
        per_hour: Opportunities per rule per hour at each store.
        default_compliance: The chance an opportunity is met when a profile does not say.
        stores: Store id -> its profile.
    """

    seed: int
    start: datetime
    hours: float = Field(gt=0, le=168)
    per_hour: float = Field(default=6.0, gt=0, le=120)
    default_compliance: float = Field(default=0.9, ge=0, le=1)
    stores: dict[str, StoreProfile] = Field(default_factory=dict)


def load_simulation(path: Path) -> Simulation:
    """Load a simulation profile.

    Raises:
        ObservationsError: The file cannot be read or does not validate.
    """
    try:
        return Simulation.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        msg = f"cannot load simulation {path}: {exc}"
        raise ObservationsError(msg) from exc


def _coverage(
    cameras: Sequence[str], profile: StoreProfile, sim: Simulation
) -> dict[str, tuple[CoverageSegment, ...]]:
    end = sim.start + timedelta(hours=sim.hours)
    out: dict[str, tuple[CoverageSegment, ...]] = {}
    for camera in cameras:
        spans = [
            (sim.start + timedelta(hours=b.from_h), timedelta(hours=b.hours), b.reason)
            for b in profile.blind.get(camera, ())
        ]
        segments = []
        at = sim.start
        while at < end:
            stop = min(at + SEGMENT, end)
            reason = next((r for s, d, r in spans if s < stop and at < s + d), None)
            segments.append(
                CoverageSegment(start=at, end=stop, watched=reason is None, reason=reason)
            )
            at = stop
        out[camera] = tuple(segments)
    return out


def _outcomes(
    fit: StoreFit, profile: StoreProfile, sim: Simulation, rng: np.random.Generator
) -> tuple[RuleOutcome, ...]:
    count = round(sim.hours * sim.per_hour)
    outcomes = []
    for rule in fit.rules:
        if rule.status != "ready" or not rule.cameras:
            continue
        chance = profile.compliance.get(rule.rule_id, sim.default_compliance)
        offsets = np.sort(rng.uniform(0.0, sim.hours * 3600.0 - OUTCOME_SPAN.seconds, count))
        for i, offset in enumerate(offsets):
            start = sim.start + timedelta(seconds=float(offset))
            outcomes.append(
                RuleOutcome(
                    rule_id=rule.rule_id,
                    camera_id=rule.cameras[i % len(rule.cameras)],
                    start=start,
                    end=start + OUTCOME_SPAN,
                    compliant=bool(rng.random() < chance),
                )
            )
    return tuple(outcomes)


def simulate(fits: Sequence[StoreFit], sim: Simulation) -> ChainObservations:
    """SIMULATED observations for every store's ready rules.

    Args:
        fits: Every store's fit.
        sim: The simulation.

    Returns:
        Observations with `Provenance.SIMULATED`, the same for the same seed.
    """
    stores = {}
    for index, fit in enumerate(sorted(fits, key=lambda f: f.store_id)):
        rng = np.random.default_rng([sim.seed, index])
        profile = sim.stores.get(fit.store_id, StoreProfile())
        cameras = sorted({c for r in fit.rules if r.status == "ready" for c in r.cameras})
        stores[fit.store_id] = StoreObservations(
            outcomes=_outcomes(fit, profile, sim, rng),
            coverage=_coverage(cameras, profile, sim),
        )
    return ChainObservations(
        provenance=Provenance.SIMULATED,
        start=sim.start,
        end=sim.start + timedelta(hours=sim.hours),
        stores=stores,
    )
