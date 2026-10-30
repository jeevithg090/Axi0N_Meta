"""Errors raised by model clients."""

from __future__ import annotations

from rulecam.errors import RulecamError


class ModelClientError(RulecamError):
    """Base class for every model-client failure."""


class ModelHTTPError(ModelClientError):
    """The model API answered with a non-success HTTP status.

    Attributes:
        status: The HTTP status code of the last attempt.
        attempts: How many attempts were made before giving up.
    """

    def __init__(self, message: str, *, status: int, attempts: int) -> None:
        """Create the error.

        Args:
            message: Human-readable description, including the API's error message if any.
            status: The HTTP status code of the last attempt.
            attempts: How many attempts were made before giving up.
        """
        super().__init__(message)
        self.status = status
        self.attempts = attempts


class ModelResponseError(ModelClientError):
    """The model API answered successfully but the body could not be understood."""


class ClientConfigError(ModelClientError):
    """A client cannot be built from the current settings (missing key or base URL)."""
