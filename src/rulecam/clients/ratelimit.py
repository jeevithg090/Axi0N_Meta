"""A thread-safe token-bucket rate limiter for model clients.

Rate limits are per team (3,000 RPM for `sam-3.1`), so clients budget below the limit and drain the
bucket when the API reports that no requests remain (`x-ratelimit-remaining-requests: 0`).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

SECONDS_PER_MINUTE = 60.0


class TokenBucket:
    """Classic token bucket: `rate_per_minute` tokens refill continuously up to `capacity`.

    `acquire()` blocks the calling thread until a token is available, so it is safe to call from
    worker threads started with `asyncio.to_thread`.
    """

    def __init__(
        self,
        rate_per_minute: float,
        *,
        capacity: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create a full bucket.

        Args:
            rate_per_minute: Sustained request budget per minute; must be positive.
            capacity: Largest burst allowed; defaults to one second of budget (at least 1).
            monotonic: Time source in seconds (injected in tests).
            sleep: Blocking sleep in seconds (injected in tests).

        Raises:
            ValueError: If `rate_per_minute` or `capacity` is not positive.
        """
        if rate_per_minute <= 0:
            msg = f"rate_per_minute must be positive, got {rate_per_minute}"
            raise ValueError(msg)
        self._rate_per_s = rate_per_minute / SECONDS_PER_MINUTE
        self._capacity = capacity if capacity is not None else max(1.0, self._rate_per_s)
        if self._capacity <= 0:
            msg = f"capacity must be positive, got {self._capacity}"
            raise ValueError(msg)
        self._monotonic = monotonic
        self._sleep = sleep
        self._tokens = self._capacity
        self._updated = monotonic()
        self._lock = threading.Lock()

    @property
    def rate_per_minute(self) -> float:
        """The sustained budget in requests per minute."""
        return self._rate_per_s * SECONDS_PER_MINUTE

    def _refill(self) -> None:
        now = self._monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate_per_s)
        self._updated = now

    def try_acquire(self) -> float:
        """Take a token if one is available.

        Returns:
            0.0 when a token was taken, otherwise the seconds to wait before one is available.
        """
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0
            return (1.0 - self._tokens) / self._rate_per_s

    def acquire(self) -> None:
        """Block until a token is available, then take it."""
        while (wait_s := self.try_acquire()) > 0:
            self._sleep(wait_s)

    def drain(self) -> None:
        """Empty the bucket, e.g. when the API reports zero remaining requests."""
        with self._lock:
            self._refill()
            self._tokens = 0.0
