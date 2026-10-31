"""Errors raised by the action layer."""

from __future__ import annotations

from rulecam.errors import RulecamError


class ActionError(RulecamError):
    """Base class for action failures."""


class DeviceError(ActionError):
    """A plug, light or speaker did not respond."""


class ResumeRefusedError(ActionError):
    """An interlock resume was attempted without a person's role."""
