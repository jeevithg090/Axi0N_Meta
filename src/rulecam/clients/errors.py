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


class ModelTruncatedError(ModelResponseError):
    """The reply was cut at the output cap (`finish_reason: length`) even at the highest cap.

    Attributes:
        max_tokens: The cap of the last attempt.
    """

    def __init__(self, message: str, *, max_tokens: int) -> None:
        """Create the error.

        Args:
            message: Human-readable description, with the token usage when known.
            max_tokens: The cap of the last attempt.
        """
        super().__init__(message)
        self.max_tokens = max_tokens


class StructuredOutputError(ModelResponseError):
    """The model output could not be validated against the schema even after retry."""


class ClientConfigError(ModelClientError):
    """A client cannot be built from the current settings (missing key or base URL)."""
