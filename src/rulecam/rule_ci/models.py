"""Data models for Rule CI (task P2-08, MP §3.4, V2 Pillar 3)."""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from rulecam.contracts.core import Calibration, Frozen, Track
from rulecam.contracts.rule_ir import RuleIR

REQUIRED_KILL_RATIO: Final = 0.90

TraceKind = Literal["boundary", "gap", "window", "interruption", "id_swap", "order", "zone_edge"]


class TraceCase(Frozen):
    """A synthetic track set built from a rule's thresholds, with its expected outcome.

    Attributes:
        name: Unique, readable name, e.g. `total_s_gte_below_19.5s`.
        description: What the trace does and why the outcome is expected.
        kind: Which family of variant this is.
        tracks: The synthetic tracks.
        calibration: The synthetic camera's calibration.
        t0: Window start.
        t1: Window end.
        expected_violation: Whether the rule must fire, derived from how the trace was built.
        pair: For a boundary pair, the threshold it straddles (the two members share it).
        value: The value the trace puts on that threshold's side (seconds, metres, m/s).
    """

    name: str
    description: str
    kind: TraceKind
    tracks: tuple[Track, ...]
    calibration: Calibration
    t0: datetime
    t1: datetime
    expected_violation: bool
    pair: str | None = None
    value: float | None = None


class MutationCase(Frozen):
    """One mutant of a rule.

    Attributes:
        name: Unique mutant name, e.g. `widen_zone:line_entry`.
        operator: The operator that made it (see `rule_ci.mutations.OPERATORS`).
        rule: The mutated rule.
        description: What changed.
    """

    name: str
    operator: str
    rule: RuleIR
    description: str


class RuleCIReport(Frozen):
    """Outcome of boundary traces and mutation tests for one rule (published on `ci.result`).

    Attributes:
        rule_id: The rule.
        traces_passed: Traces whose observed outcome matched the expected one.
        traces_total: Traces run.
        mutants_killed: Mutants for which at least one trace's outcome changed.
        mutants_total: Mutants run.
        failures: Failing traces, surviving mutants and generator problems, readable.
        nonvacuous: The rule fired on the positive member and stayed silent on the negative
            member of at least one boundary pair.
        justification: Recorded reason to accept a kill ratio below `REQUIRED_KILL_RATIO`.
    """

    rule_id: str
    traces_passed: int
    traces_total: int
    mutants_killed: int
    mutants_total: int
    failures: tuple[str, ...] = ()
    nonvacuous: bool
    justification: str | None = None

    @property
    def mutation_score(self) -> float:
        """Killed over total mutants (0 when there are none)."""
        return self.mutants_killed / self.mutants_total if self.mutants_total else 0.0

    @property
    def ci_green(self) -> bool:
        """All traces pass and at least 90% of mutants die, or a justification is recorded."""
        if self.traces_total == 0 or self.traces_passed < self.traces_total:
            return False
        if self.mutants_total and self.mutation_score >= REQUIRED_KILL_RATIO:
            return True
        return bool(self.justification and self.justification.strip())

    def summary(self) -> str:
        """E.g. `boundary 16/16, mutations 5/5`."""
        return (
            f"boundary {self.traces_passed}/{self.traces_total}, "
            f"mutations {self.mutants_killed}/{self.mutants_total}"
        )


class CIResult(Frozen):
    """Payload of `ci.result`: `{rule_id, report}`, plus the verdict the UI shows.

    Attributes:
        rule_id: The rule.
        report: Its Rule CI report.
        ci_green: `report.ci_green`.
        summary: `report.summary()`.
    """

    rule_id: str
    report: RuleCIReport
    ci_green: bool
    summary: str

    @classmethod
    def of(cls, report: RuleCIReport) -> CIResult:
        """The payload for a report."""
        return cls(
            rule_id=report.rule_id,
            report=report,
            ci_green=report.ci_green,
            summary=report.summary(),
        )
