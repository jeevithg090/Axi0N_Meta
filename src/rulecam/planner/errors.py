"""Errors raised by the planner."""

from __future__ import annotations

from rulecam.errors import RulecamError


class PlannerError(RulecamError):
    """Base error for planner operations."""


class IntakeError(PlannerError):
    """A request item cannot be taken in (missing file, wrong file type, no uploader)."""
