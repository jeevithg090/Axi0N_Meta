"""Owner login (WS4): a passphrase gives a signed session cookie that owner routes accept.

Owner routes used to accept only this machine (`is_loopback_owner`: loopback host and client),
so on the deployed stack the owner could act only over SSH. `OwnerAuth.require_owner` replaces
Pair-a-Phone's `require_owner` in the API app (`app.dependency_overrides`) and accepts either:

- **loopback**, exactly as before (SSH, `next dev` on the owner's machine); or
- **a valid owner session**: the `__Host-rulecam_owner` cookie, issued by `POST /auth/login`.
  It is `HttpOnly; Secure; SameSite=Strict; Path=/`, with no `Domain`, so behind the Vercel
  rewrite it belongs to the UI's own host and rides along with every same-origin
  `/rulecam-api/*` call. Its value is `<payload>.<HMAC-SHA256>` (base64url); the key is derived
  from `SESSION_SECRET` *and* the passphrase hash, so rotating either signs every session out.
  It expires after `SESSION_TTL_S` (12 h); `POST /auth/logout` also revokes it in memory.

A state-changing request (anything but GET/HEAD/OPTIONS) that relies on a session must also
carry an `Origin` in the allowed set (the public UI's, `PUBLIC_UI_URL`) and an `X-CSRF-Token`
header equal to the token bound inside the signed cookie (`GET /auth/me` returns it). A
WebSocket on a session needs an allowed `Origin` or none.

`POST /auth/login` checks the passphrase against `OWNER_PASSPHRASE_HASH` (`api.passphrase`,
constant-time), only over HTTPS (or to localhost), and is rate-limited: 5 attempts a minute per
client and 20 a minute in total, the total being what bounds guessing when many clients share
one address behind a proxy. The client address is the TCP peer as uvicorn resolved it from the
trusted proxy headers, or, when the request carries the shared `UI_PROXY_SECRET` (set by the
UI's middleware), the browser address the UI forwarded (`ClientAddress`).

Without `OWNER_PASSPHRASE_HASH` and `SESSION_SECRET` login answers 503 and only loopback is the
owner, as before.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import math
import secrets
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Final, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from starlette.requests import HTTPConnection

from rulecam.api.passphrase import MAX_PASSPHRASE_CHARS, PassphraseHash
from rulecam.capture.pair_phone import is_loopback, is_loopback_owner
from rulecam.config import Settings
from rulecam.contracts import Clock
from rulecam.log import get_logger

_log = get_logger(__name__)

SESSION_COOKIE: Final = "__Host-rulecam_owner"
CSRF_HEADER: Final = "X-CSRF-Token"
CLIENT_IP_HEADER: Final = "X-Rulecam-Client-IP"
PROXY_SECRET_HEADER: Final = "X-Rulecam-Proxy-Secret"  # noqa: S105 - a header name
SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
MIN_SECRET_CHARS: Final = 32
DEFAULT_TTL_S: Final = 12 * 3600
LOGIN_WINDOW_S: Final = 60.0
LOGIN_PER_CLIENT: Final = 5
LOGIN_TOTAL: Final = 20
MAX_TRACKED_CLIENTS: Final = 4096
MAX_TOKEN_CHARS: Final = 1024
OWNER_REQUIRED: Final = "owner login required"
_KEY_CONTEXT: Final = b"rulecam-owner-session-v1\x00"

OwnerVia = Literal["loopback", "session"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class OwnerSession(BaseModel):
    """What the signed cookie carries: a session id, its CSRF token and its expiry (epoch s)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sid: str
    csrf: str
    exp: int


class SessionSigner:
    """Issues and checks `<payload>.<signature>` session tokens (HMAC-SHA256)."""

    def __init__(self, secret: str, binding: str) -> None:
        """Derive the signing key from the server secret and `binding` (the passphrase hash)."""
        self._key = hmac.new(
            secret.encode("utf-8"), _KEY_CONTEXT + binding.encode("utf-8"), hashlib.sha256
        ).digest()

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())

    def issue(self, now: datetime, ttl_s: int) -> tuple[str, OwnerSession]:
        """A fresh session valid for `ttl_s` seconds from `now`, and its token."""
        session = OwnerSession(
            sid=secrets.token_urlsafe(16),
            csrf=secrets.token_urlsafe(32),
            exp=int(now.timestamp()) + ttl_s,
        )
        body = _b64(session.model_dump_json().encode("utf-8"))
        return f"{body}.{self._sign(body)}", session

    def read(self, token: str, now: datetime) -> OwnerSession | None:
        """The session in a token, or `None` if it is malformed, forged or expired."""
        body, _, signature = token.partition(".")
        if not body or not signature or len(token) > MAX_TOKEN_CHARS or not token.isascii():
            return None
        if not hmac.compare_digest(signature, self._sign(body)):
            return None
        try:
            session = OwnerSession.model_validate_json(_unb64(body))
        except (ValidationError, binascii.Error, ValueError):
            return None
        return session if session.exp > now.timestamp() else None


class LoginLimiter:
    """Sliding-window limit on login attempts, per client and in total (thread-safe)."""

    def __init__(
        self,
        *,
        per_client: int = LOGIN_PER_CLIENT,
        total: int = LOGIN_TOTAL,
        window_s: float = LOGIN_WINDOW_S,
    ) -> None:
        """Allow `per_client` attempts per client and `total` overall per `window_s` seconds."""
        self._per_client = per_client
        self._total = total
        self._window = window_s
        self._clients: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def attempt(self, client: str, now: float) -> float:
        """Record an attempt at `now` (seconds) if allowed.

        Returns:
            0 when the attempt may go ahead, else the seconds until one may.
        """
        with self._lock:
            self._prune(now)
            mine = self._clients.get(client)
            if mine is not None and len(mine) >= self._per_client:
                return mine[0] + self._window - now
            if len(self._all) >= self._total:
                return self._all[0] + self._window - now
            if mine is None:
                if len(self._clients) >= MAX_TRACKED_CLIENTS:
                    self._clients.pop(next(iter(self._clients)))
                mine = self._clients[client] = deque()
            mine.append(now)
            self._all.append(now)
            return 0.0

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._all and self._all[0] <= cutoff:
            self._all.popleft()
        for client in list(self._clients):
            times = self._clients[client]
            while times and times[0] <= cutoff:
                times.popleft()
            if not times:
                del self._clients[client]


class ClientAddress:
    """The client's address for rate limits, trusting the UI's forwarded one only with the secret.

    Behind Vercel and the RunPod proxy, the TCP peer uvicorn reports is a proxy, so every browser
    would share one limit. The UI's middleware sends the browser's address in
    `X-Rulecam-Client-IP` together with `X-Rulecam-Proxy-Secret`; without the right secret
    (anyone can call the API's own URL) the header is ignored.
    """

    def __init__(self, proxy_secret: str | None = None) -> None:
        """Trust `X-Rulecam-Client-IP` only next to `proxy_secret` (never when it is `None`)."""
        self._secret = proxy_secret.encode("utf-8") if proxy_secret else None

    def __call__(self, conn: HTTPConnection) -> str:
        """The address to count `conn` under."""
        direct = conn.client.host if conn.client else ""
        given = conn.headers.get(PROXY_SECRET_HEADER)
        if self._secret is None or given is None:
            return direct
        if not hmac.compare_digest(given.encode("utf-8"), self._secret):
            return direct
        try:
            return str(ipaddress.ip_address(conn.headers.get(CLIENT_IP_HEADER, "").strip()))
        except ValueError:
            return direct


class LoginRequest(BaseModel):
    """Body of `POST /auth/login`."""

    model_config = ConfigDict(extra="forbid")

    passphrase: str = Field(min_length=1, max_length=MAX_PASSPHRASE_CHARS)


class AuthStatus(BaseModel):
    """Body of `GET /auth/me` and `POST /auth/login`.

    Attributes:
        owner: Whether this request is the owner's.
        via: How: from this machine, or with a session.
        csrf_token: The session's token for the `X-CSRF-Token` header on writes.
        expires_at: When the session ends.
        login_enabled: Whether passphrase login is set up on this server.
    """

    owner: bool
    via: OwnerVia | None = None
    csrf_token: str | None = None
    expires_at: datetime | None = None
    login_enabled: bool


def _configure(
    passphrase_hash: str | None, session_secret: str | None
) -> tuple[PassphraseHash, SessionSigner] | str:
    """The parsed hash and the signer, or why login stays off."""
    if not passphrase_hash or not session_secret:
        return "OWNER_PASSPHRASE_HASH and SESSION_SECRET are both needed"
    if len(session_secret) < MIN_SECRET_CHARS:
        return f"SESSION_SECRET must have at least {MIN_SECRET_CHARS} characters"
    try:
        parsed = PassphraseHash.parse(passphrase_hash)
    except ValueError as exc:
        return f"OWNER_PASSPHRASE_HASH is invalid: {exc}"
    return parsed, SessionSigner(session_secret, parsed.line())


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail)


class OwnerAuth:
    """Who the owner is: this machine, or a browser with a signed session."""

    def __init__(
        self,
        *,
        clock: Clock,
        passphrase_hash: str | None = None,
        session_secret: str | None = None,
        allowed_origins: frozenset[str] = frozenset(),
        ttl_s: int = DEFAULT_TTL_S,
        client_address: ClientAddress | None = None,
        limiter: LoginLimiter | None = None,
    ) -> None:
        """Set up login; without both a valid hash and secret only loopback is the owner.

        Args:
            clock: Time source for expiry and the rate limit.
            passphrase_hash: The `scrypt:` line (`rulecam owner set-passphrase`).
            session_secret: Signs the session cookies (32+ characters).
            allowed_origins: Browser origins a session may write from (the public UI's).
            ttl_s: Session lifetime.
            client_address: Resolves the client for the rate limit.
            limiter: The login rate limit.
        """
        self._clock = clock
        self._origins = allowed_origins
        self._ttl_s = ttl_s
        self._client = client_address or ClientAddress()
        self._limiter = limiter or LoginLimiter()
        self._revoked: dict[str, int] = {}
        self._lock = threading.Lock()
        configured = _configure(passphrase_hash, session_secret)
        if isinstance(configured, str):
            self._hash: PassphraseHash | None = None
            self._signer: SessionSigner | None = None
            if passphrase_hash or session_secret:
                _log.error("owner_login_disabled", reason=configured)
        else:
            self._hash, self._signer = configured

    @classmethod
    def from_settings(
        cls, settings: Settings, *, clock: Clock, allowed_origins: frozenset[str]
    ) -> OwnerAuth:
        """Build from the owner-login settings (hash, secret, TTL and the UI proxy secret)."""

        def reveal(value: SecretStr | None) -> str | None:
            return value.get_secret_value() if value is not None else None

        return cls(
            clock=clock,
            passphrase_hash=reveal(settings.owner_passphrase_hash),
            session_secret=reveal(settings.session_secret),
            allowed_origins=allowed_origins,
            ttl_s=settings.session_ttl_s,
            client_address=ClientAddress(reveal(settings.ui_proxy_secret)),
        )

    @property
    def login_enabled(self) -> bool:
        """Whether passphrase login is set up."""
        return self._signer is not None

    def session(self, conn: HTTPConnection) -> OwnerSession | None:
        """The valid, unrevoked owner session `conn` carries, if any."""
        token = conn.cookies.get(SESSION_COOKIE)
        if token is None or self._signer is None:
            return None
        session = self._signer.read(token, self._clock.now())
        with self._lock:
            revoked = session is not None and session.sid in self._revoked
        return None if revoked else session

    def require_owner(self, conn: HTTPConnection) -> None:
        """The owner-route dependency: loopback, or a session (plus Origin and CSRF on writes).

        Raises:
            HTTPException: 403 otherwise.
        """
        if is_loopback_owner(conn):
            return
        session = self.session(conn)
        if session is None:
            raise _forbidden(OWNER_REQUIRED)
        writes = conn.scope["type"] == "http" and conn.scope["method"] not in SAFE_METHODS
        self._check_origin(conn, required=writes)
        if writes:
            self._check_csrf(conn, session)

    def _check_origin(self, conn: HTTPConnection, *, required: bool) -> None:
        origin = conn.headers.get("origin")
        if origin is None and not required:
            return
        if origin not in self._origins:
            raise _forbidden("this request must come from the Rulecam web UI")

    @staticmethod
    def _check_csrf(conn: HTTPConnection, session: OwnerSession) -> None:
        given = conn.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(given.encode("utf-8"), session.csrf.encode("utf-8")):
            raise _forbidden("missing or wrong CSRF token; reload the page")

    def status_of(self, conn: HTTPConnection) -> AuthStatus:
        """Whether `conn` is the owner's, and how."""
        if is_loopback_owner(conn):
            return AuthStatus(owner=True, via="loopback", login_enabled=self.login_enabled)
        session = self.session(conn)
        if session is None:
            return AuthStatus(owner=False, login_enabled=self.login_enabled)
        return AuthStatus(
            owner=True,
            via="session",
            csrf_token=session.csrf,
            expires_at=datetime.fromtimestamp(session.exp, UTC),
            login_enabled=True,
        )

    def login(self, body: LoginRequest, request: Request, response: Response) -> AuthStatus:
        """Check the passphrase and set the session cookie.

        Raises:
            HTTPException: 403 over plain HTTP or from a foreign page, 503 when login is not
                set up, 429 when rate-limited, 401 for a wrong passphrase.
        """
        if request.url.scheme != "https" and not is_loopback(request.url.hostname):
            raise _forbidden("log in over HTTPS")
        self._check_origin(request, required=False)
        if self._hash is None or self._signer is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "owner login is not set up on this server"
            )
        client = self._client(request)
        now = self._clock.now()
        wait_s = self._limiter.attempt(client, now.timestamp())
        if wait_s > 0:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many login attempts; try again in a minute",
                headers={"Retry-After": str(max(1, math.ceil(wait_s)))},
            )
        if not self._hash.verify(body.passphrase):
            _log.warning("owner_login_failed", client=client)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong passphrase")
        token, session = self._signer.issue(now, self._ttl_s)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=self._ttl_s,
            path="/",
            secure=True,
            httponly=True,
            samesite="strict",
        )
        response.headers["Cache-Control"] = "no-store"
        _log.info("owner_login", client=client)
        return AuthStatus(
            owner=True,
            via="session",
            csrf_token=session.csrf,
            expires_at=datetime.fromtimestamp(session.exp, UTC),
            login_enabled=True,
        )

    def logout(self, request: Request, response: Response) -> None:
        """Revoke the session (a write: Origin and CSRF are checked) and clear the cookie."""
        session = self.session(request)
        if session is not None:
            self._check_origin(request, required=True)
            self._check_csrf(request, session)
            with self._lock:
                now = int(self._clock.now().timestamp())
                self._revoked = {sid: exp for sid, exp in self._revoked.items() if exp > now}
                self._revoked[session.sid] = session.exp
            _log.info("owner_logout")
        response.delete_cookie(
            SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict"
        )

    def me(self, request: Request, response: Response) -> AuthStatus:
        """Who is asking (never cached)."""
        response.headers["Cache-Control"] = "no-store"
        return self.status_of(request)

    def router(self) -> APIRouter:
        """`POST /auth/login`, `POST /auth/logout` and `GET /auth/me`."""
        router = APIRouter(prefix="/auth", tags=["auth"])
        router.add_api_route(
            "/login",
            self.login,
            methods=["POST"],
            responses={
                401: {"description": "Wrong passphrase"},
                429: {"description": "Too many attempts"},
                503: {"description": "Login not set up"},
            },
        )
        router.add_api_route(
            "/logout", self.logout, methods=["POST"], status_code=status.HTTP_204_NO_CONTENT
        )
        router.add_api_route("/me", self.me, methods=["GET"])
        return router
