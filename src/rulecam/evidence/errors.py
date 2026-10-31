"""Errors raised by the evidence package."""

from __future__ import annotations

from rulecam.errors import RulecamError


class EvidenceError(RulecamError):
    """Base class for evidence failures."""


class ChainError(EvidenceError):
    """A chain file cannot be extended (its last line is not a record)."""
