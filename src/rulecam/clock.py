"""Real clock implementation of the `Clock` protocol (tests use `tests.fakes.FakeClock`)."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Wall-clock time in UTC."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(UTC)
