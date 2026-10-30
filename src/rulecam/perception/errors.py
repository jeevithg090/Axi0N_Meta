"""Errors raised by the perception package."""

from rulecam.errors import RulecamError


class PerceptionError(RulecamError):
    """Base class for perception errors."""


class CalibrationError(PerceptionError):
    """A ground-plane calibration could not be fitted or applied."""


class ZoneError(PerceptionError):
    """A zone is malformed or cannot be built."""
