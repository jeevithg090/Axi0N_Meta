"""Errors raised by the Site Advisor."""

from __future__ import annotations

from rulecam.errors import RulecamError


class AdvisorError(RulecamError):
    """Base error for Site Advisor operations."""


class ClaimError(AdvisorError):
    """A claim template breaks the slot-only or station-not-person rules (SA §4 step 13, §5)."""


class EvidenceError(AdvisorError):
    """Measured evidence is unusable: grouped by track, mixed provenance, or wrong unit."""


class AskError(AdvisorError):
    """The Ask step's answer names a metric outside the catalogue or an invalid window."""
