"""Errors raised by the worker package."""

from __future__ import annotations

from rulecam.errors import RulecamError


class WorkerError(RulecamError):
    """Base class for worker page and dispute failures."""


class UnknownEventError(WorkerError):
    """No recent event has the given short code."""


class DisputeLimitError(WorkerError):
    """A dispute was refused by the abuse bounds (per client, per day or per event)."""
