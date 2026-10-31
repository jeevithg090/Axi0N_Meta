"""Data models for Rule CI (task P2-08, MP §3.4, V2 Pillar 3)."""

from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from rulecam.contracts.core import Calibration, Frozen, Track, Zone
from rulecam.contracts.rule_ir import RuleIR

REQUIRED_MUTANTS_RATIO: Final = 0.90


class TraceCase(BaseModel):
    """A synthetic trace case with its expected outcome."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    tracks: list[Track]
    zones: dict[str, Zone]
    calibration: Calibration
    t0: datetime
    t1: datetime
    expected_violation: bool
    threshold_tested: float | None = None
    variant_kind: str = "boundary"


class MutationCase(BaseModel):
    """One mutated variant of a RuleIR."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    operator: str
    mutated_rule: RuleIR
    description: str = ""


class RuleCIReport(Frozen):
    """Outcome report of boundary-trace testing and mutation fuzzing for a rule."""

    rule_id: str
    traces_passed: int
    traces_total: int
    mutants_killed: int
    mutants_total: int
    failures: list[str] = Field(default_factory=list)
    nonvacuous: bool = True
    justification: str | None = None

    @property
    def ci_green(self) -> bool:
        """ci_green requires all traces passing, nonvacuous=True, and >= 90% of mutants killed."""
        if not self.nonvacuous:
            return False
        if self.traces_total == 0 or self.traces_passed < self.traces_total:
            return False
        if self.mutants_total == 0:
            return True
        ratio = self.mutants_killed / self.mutants_total
        if ratio >= REQUIRED_MUTANTS_RATIO:
            return True
        return bool(self.justification and self.justification.strip())
