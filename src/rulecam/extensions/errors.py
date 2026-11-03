"""Errors raised by rule extensions (model-written check functions)."""

from __future__ import annotations

from rulecam.errors import RulecamError


class ExtensionError(RulecamError):
    """Base class for rule-extension errors."""


class ExtensionRejectedError(ExtensionError):
    """The code broke a validator rule; it is never run.

    Attributes:
        reasons: One short line per broken rule.
    """

    def __init__(self, reasons: list[str]) -> None:
        """Create the error.

        Args:
            reasons: One short line per broken rule.
        """
        self.reasons = reasons
        super().__init__("extension rejected: " + "; ".join(reasons))


class SandboxError(ExtensionError):
    """A sandbox run failed as a whole: killed, timed out or answered garbage."""


class RegistryError(ExtensionError):
    """An extension is unknown, or a status change is not allowed."""
