"""Errors raised by the planner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rulecam.errors import RulecamError

if TYPE_CHECKING:
    from rulecam.planner.guardrails import Declined


class PlannerError(RulecamError):
    """Base error for planner operations."""


class IntakeError(PlannerError):
    """A request item cannot be taken in (missing file, wrong file type, no uploader)."""


class RuleCompilationError(PlannerError):
    """Raised when rule compilation fails and cannot be repaired."""


class GuardrailDeclinedError(PlannerError):
    """Raised when a request is declined by guardrails.

    Attributes:
        declined: The decline, with its category, reason and alternative.
    """

    def __init__(self, declined: Declined) -> None:
        """Create the error.

        Args:
            declined: The guardrail decline.
        """
        super().__init__(f"Request declined under {declined.category}: {declined.reason}")
        self.declined = declined


class StandardsFetchError(PlannerError):
    """A cited standard's page cannot be fetched or read as text."""


class PipelineError(PlannerError):
    """The fixed-step pipeline cannot run or resume (bad plan id, unknown step, missing output)."""
