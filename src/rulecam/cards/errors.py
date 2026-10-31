"""Errors raised by the cards package."""

from __future__ import annotations

from rulecam.errors import RulecamError


class CardError(RulecamError):
    """A card cannot be built or rendered."""
