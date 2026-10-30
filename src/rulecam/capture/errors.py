"""Errors raised by the capture package."""

from rulecam.errors import RulecamError


class CaptureError(RulecamError):
    """Capture failed: ffmpeg could not start, or a chunk could not be probed."""
