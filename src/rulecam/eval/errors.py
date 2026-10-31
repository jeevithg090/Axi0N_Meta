"""Errors of the eval harness."""

from __future__ import annotations

from rulecam.errors import RulecamError


class EvalError(RulecamError):
    """Base class for eval harness errors."""


class EvalSetError(EvalError):
    """An eval-set entry is missing, malformed or inconsistent."""


class ClipFetchError(EvalError):
    """A clip could not be found, downloaded or verified against its sha256."""


class EvalRunError(EvalError):
    """A run against the API could not complete (pairing, planning or approval failed)."""
