"""Errors raised by the planner and rule compilation."""

from __future__ import annotations

from rulecam.errors import RulecamError


class PlannerError(RulecamError):
    """Base error for planner operations."""


class RuleCompilationError(PlannerError):
    """Raised when rule compilation fails and cannot be repaired."""


class GuardrailDeclinedError(PlannerError):
    """Raised when a request is declined by guardrails."""


class IntakeError(PlannerError):
    """A request item cannot be taken in (missing file, wrong file type, no uploader)."""
