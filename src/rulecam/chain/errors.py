"""Errors raised by Chain Mode."""

from __future__ import annotations

from rulecam.errors import RulecamError


class ChainError(RulecamError):
    """Base error for Chain Mode."""


class PackError(ChainError):
    """A rule pack file cannot be read or does not validate."""


class StoreSetupError(ChainError):
    """A store setup file cannot be read or does not validate."""


class ObservationsError(ChainError):
    """Store observations cannot be scored (mixed provenance, bad window, unknown store)."""


class RollupError(ChainError):
    """A store's evidence root cannot be rolled up or proven."""
