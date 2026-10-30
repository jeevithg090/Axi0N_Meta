"""Errors raised by the perception package (§5.3)."""

from rulecam.errors import RulecamError


class PerceptionError(RulecamError):
    """Base class for perception errors."""


class CalibrationError(PerceptionError):
    """A ground-plane calibration could not be fitted or applied."""


class ZoneError(PerceptionError):
    """A zone is malformed or cannot be built."""


class SamRuntimeError(PerceptionError):
    """The self-hosted SAM 3.1 runtime failed to serve a call.

    The segmenter router catches exactly this error (and its subclasses) to fall back to hosted
    SAM 3.1. Invalid prompts raise `ValueError` instead and never trigger a fallback.
    """


class SamRuntimeUnavailableError(SamRuntimeError):
    """The runtime could not be reached: Modal is down, the app is not deployed, no `ml` extra."""


class SamResponseError(SamRuntimeError):
    """A SAM worker answered with a response that does not match the wire format."""
