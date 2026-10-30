"""Errors raised by the rules engine."""

from __future__ import annotations

from rulecam.errors import RulecamError


class RuleEvaluationError(RulecamError):
    """A rule cannot be evaluated with the given inputs (bad window, unknown zone, …)."""


class UnsupportedPredicateError(RuleEvaluationError):
    """The rule uses a predicate op this version of the engine does not evaluate yet."""
