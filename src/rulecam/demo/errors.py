"""Errors of the golden demo runs."""

from __future__ import annotations

from rulecam.errors import RulecamError


class DemoError(RulecamError):
    """Base class for golden demo failures."""


class FixtureError(DemoError):
    """A golden fixture is missing a file, or a file is malformed."""


class CaptureError(DemoError):
    """A live run cannot be frozen into a fixture (not recorded, strips or tracks missing)."""
