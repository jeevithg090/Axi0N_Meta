"""Errors raised by the replay layer (cache, golden runs, preflight)."""

from __future__ import annotations

from rulecam.errors import RulecamError


class ReplayError(RulecamError):
    """Base class for replay-layer failures."""


class ReplayMiss(ReplayError):  # noqa: N818 - name fixed by the task book (§5.2)
    """Replay mode was asked for a call that was never recorded. The network is never used.

    Attributes:
        provider: The API provider of the missing call.
        model: The model ID of the missing call.
        key: The cache key that was looked up.
    """

    def __init__(self, *, provider: str, model: str, key: str) -> None:
        """Create the error.

        Args:
            provider: The API provider of the missing call.
            model: The model ID of the missing call.
            key: The cache key that was looked up.
        """
        super().__init__(
            f"no recorded reply for {provider}/{model} (key {key[:12]}…); "
            "record it with RULECAM_MODE=record or `make golden`"
        )
        self.provider = provider
        self.model = model
        self.key = key


class ReplayCorruptError(ReplayError):
    """A cache entry exists but cannot be parsed."""


class ScenarioError(ReplayError):
    """A golden-run scenario file is invalid or names an unknown step."""
