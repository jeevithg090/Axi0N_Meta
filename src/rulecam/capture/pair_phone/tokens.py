"""Single-use pairing tokens, phone sessions and a per-token rate limit (task P1-09).

A token pairs exactly one phone. The owner UI issues it; the phone claims it within
`TOKEN_TTL` and receives a session key, which it presents on every later upload and on the
coach WebSocket. A second claim of the same token is rejected.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from rulecam.capture.pair_phone.errors import (
    SessionInvalidError,
    TokenExpiredError,
    TokenUnknownError,
    TokenUsedError,
)
from rulecam.contracts import Clock

TOKEN_TTL = timedelta(minutes=10)
TOKEN_BYTES = 16
SESSION_BYTES = 24


class PairedCamera(BaseModel):
    """A phone that claimed a token and now acts as camera `phone-<n>`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    camera_id: str
    label: str
    paired_at: datetime


@dataclass(frozen=True)
class IssuedToken:
    """A freshly issued, not yet claimed pairing token."""

    token: str
    expires_at: datetime


@dataclass
class _Pairing:
    expires_at: datetime
    camera: PairedCamera | None = None
    session: str | None = None


class PairingRegistry:
    """Issues tokens, lets each be claimed once, and authenticates the paired phone."""

    def __init__(self, clock: Clock, *, ttl: timedelta = TOKEN_TTL, first_phone: int = 1) -> None:
        """Create an empty registry.

        Args:
            clock: Time source for expiry.
            ttl: How long an unclaimed token stays valid.
            first_phone: Number of the first camera (`phone-<first_phone>`).
        """
        self._clock = clock
        self._ttl = ttl
        self._next_phone = first_phone
        self._pairings: dict[str, _Pairing] = {}

    def issue(self) -> IssuedToken:
        """Issue a new single-use token and forget unclaimed tokens that have expired."""
        now = self._clock.now()
        self._pairings = {
            token: pairing
            for token, pairing in self._pairings.items()
            if pairing.camera is not None or pairing.expires_at > now
        }
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._pairings[token] = _Pairing(expires_at=now + self._ttl)
        return IssuedToken(token=token, expires_at=now + self._ttl)

    def check_open(self, token: str) -> None:
        """Check that `token` may still show the phone page.

        Raises:
            TokenUnknownError: The token was never issued.
            TokenExpiredError: The token expired before anyone claimed it.
        """
        self._live(token)

    def claim(self, token: str, label: str) -> tuple[PairedCamera, str]:
        """Pair a phone with `token`; this works exactly once per token.

        Args:
            token: The token from the QR code.
            label: Human name for the camera, e.g. "sink wall"; empty means the camera id.

        Returns:
            The new camera and the session key the phone must present from now on.

        Raises:
            TokenUnknownError: The token was never issued.
            TokenExpiredError: The token expired before it was claimed.
            TokenUsedError: The token already paired a phone.
        """
        pairing = self._live(token)
        if pairing.camera is not None:
            msg = "this pairing code was already used; create a new one"
            raise TokenUsedError(msg)
        camera_id = f"phone-{self._next_phone}"
        pairing.camera = PairedCamera(
            camera_id=camera_id, label=label or camera_id, paired_at=self._clock.now()
        )
        pairing.session = secrets.token_urlsafe(SESSION_BYTES)
        self._next_phone += 1
        return pairing.camera, pairing.session

    def authenticate(self, token: str, session: str) -> PairedCamera:
        """Return the camera paired with `token` if `session` is its session key.

        Raises:
            TokenUnknownError: The token was never issued.
            TokenExpiredError: The token expired before it was claimed.
            SessionInvalidError: The token is unclaimed or the session key is wrong.
        """
        pairing = self._live(token)
        if (
            pairing.camera is None
            or pairing.session is None
            or not secrets.compare_digest(pairing.session, session)
        ):
            msg = "unknown session for this pairing code"
            raise SessionInvalidError(msg)
        return pairing.camera

    def cameras(self) -> list[PairedCamera]:
        """Return every paired phone, in pairing order."""
        return [p.camera for p in self._pairings.values() if p.camera is not None]

    def _live(self, token: str) -> _Pairing:
        pairing = self._pairings.get(token)
        if pairing is None:
            msg = "unknown pairing code"
            raise TokenUnknownError(msg)
        if pairing.camera is None and self._clock.now() >= pairing.expires_at:
            msg = "this pairing code has expired; create a new one"
            raise TokenExpiredError(msg)
        return pairing


class RateLimiter:
    """Allows at most one request per `interval` for each key."""

    def __init__(self, clock: Clock, interval: timedelta = timedelta(seconds=1)) -> None:
        """Create a limiter.

        Args:
            clock: Time source.
            interval: Minimum spacing between accepted requests with the same key.
        """
        self._clock = clock
        self._interval = interval
        self._last: dict[str, datetime] = {}

    def retry_after(self, key: str) -> float:
        """Record a request for `key` if allowed.

        Returns:
            0.0 when the request is allowed (and counted), otherwise the seconds to wait.
        """
        now = self._clock.now()
        last = self._last.get(key)
        if last is not None and now - last < self._interval:
            return (self._interval - (now - last)).total_seconds()
        self._last[key] = now
        return 0.0
