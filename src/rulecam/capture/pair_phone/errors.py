"""Errors raised by Pair-a-Phone. The router maps each one to an HTTP status."""

from rulecam.capture.errors import CaptureError


class PairingError(CaptureError):
    """Base class for Pair-a-Phone errors."""


class TokenUnknownError(PairingError):
    """No pairing token with this value was ever issued (404)."""


class TokenExpiredError(PairingError):
    """The token was not claimed within its lifetime (410)."""


class TokenUsedError(PairingError):
    """The token already paired a phone; tokens are single-use (409)."""


class SessionInvalidError(PairingError):
    """The phone did not present the session key issued when it claimed the token (401)."""


class UnsupportedMediaError(PairingError):
    """The upload is not a WebM or MP4 video with an allowed codec (415)."""


class ChunkTooLargeError(PairingError):
    """The upload is bigger or longer than one chunk may be (413)."""


class TranscodeError(PairingError):
    """ffmpeg could not turn a valid-looking upload into an H.264 chunk (422)."""
