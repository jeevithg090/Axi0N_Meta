"""The HTTP seam shared by model clients: an injectable transport, its reply model and retries.

A `Transport` is a plain callable from a request mapping to a reply mapping, so the replay cache
(P3-02, `cached_call`) can wrap it without knowing about HTTP. Requests look like:

- `{"method": "POST", "path": "/responses", "json": {...}}`
- `{"method": "POST", "path": "/files", "form": {"purpose": "user_data"},
  "file": {"filename": "c.mp4", "content_type": "video/mp4", "content": b"..."}}`
- `{"method": "POST", "path": "/asr/transcribe", "parts": {"request": {"content_type":
  "application/json", "content": b"{...}"}, "audio": {"filename": "a.wav", ...}}}` (named
  multipart parts; `filename` is optional)

and replies like `{"status": 200, "headers": {"x-ratelimit-remaining-requests": "12"},
"json": {...}}` (header names lower-case).
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rulecam.clients.errors import ModelClientError, ModelHTTPError, ModelResponseError
from rulecam.clients.ratelimit import TokenBucket
from rulecam.log import get_logger

_log = get_logger(__name__)

Transport = Callable[[Mapping[str, object]], Mapping[str, object]]

HTTP_OK_MIN = 200
HTTP_OK_MAX = 299
HTTP_NOT_FOUND = 404
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_MIN = 500
HTTP_SERVER_ERROR_MAX = 599
REMAINING_REQUESTS_HEADER = "x-ratelimit-remaining-requests"
RETRY_AFTER_HEADER = "retry-after"
RECORDED_LATENCY_HEADER = "x-rulecam-recorded-latency-ms"  # set by the replay cache on a hit


class ModelTransportError(ModelClientError):
    """The request never got an HTTP answer (connection reset, timeout, DNS failure)."""


class HttpReply(BaseModel):
    """A validated transport reply."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    status: int
    headers: dict[str, str] = Field(default_factory=dict)
    body: object = Field(default=None, alias="json")

    @property
    def ok(self) -> bool:
        """Whether the status is 2xx."""
        return HTTP_OK_MIN <= self.status <= HTTP_OK_MAX

    @property
    def retryable(self) -> bool:
        """Whether the status is 429 or 5xx, which are worth retrying."""
        return self.status == HTTP_TOO_MANY_REQUESTS or (
            HTTP_SERVER_ERROR_MIN <= self.status <= HTTP_SERVER_ERROR_MAX
        )

    def header(self, name: str) -> str | None:
        """Return a header value by case-insensitive name, or `None`."""
        wanted = name.lower()
        return next((v for k, v in self.headers.items() if k.lower() == wanted), None)

    def error_message(self) -> str:
        """Return the API's error message from the body, if it has one."""
        if isinstance(self.body, Mapping):
            error = self.body.get("error")
            if isinstance(error, Mapping):
                return str(error.get("message", error))
            if error is not None:
                return str(error)
        return ""


def parse_reply(raw: Mapping[str, object]) -> HttpReply:
    """Validate a transport's reply mapping.

    Args:
        raw: The mapping returned by a `Transport`.

    Returns:
        The validated reply.

    Raises:
        ModelResponseError: If the mapping has no integer `status`.
    """
    try:
        return HttpReply.model_validate(raw)
    except ValidationError as exc:
        msg = "transport reply is missing a valid status"
        raise ModelResponseError(msg) from exc


@dataclass(frozen=True)
class RetryPolicy:
    """Jittered exponential backoff for 429, 5xx and transport errors.

    Attributes:
        max_attempts: Total attempts including the first one.
        base_delay_s: Delay before the first retry; doubles on each further retry.
        max_delay_s: Upper bound for one computed delay (a longer `Retry-After` still wins).
    """

    max_attempts: int = 4
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0

    def delay_s(self, retry_number: int, rng: random.Random, retry_after_s: float | None) -> float:
        """Return how long to wait before retry number `retry_number` (1-based).

        Args:
            retry_number: 1 for the first retry, 2 for the second, and so on.
            rng: Seeded random source for the jitter.
            retry_after_s: The server's `Retry-After` in seconds, if it sent one.

        Returns:
            The delay in seconds: the backoff scaled by a jitter in [0.5, 1.0], and never less
            than `retry_after_s`.
        """
        backoff = min(self.max_delay_s, self.base_delay_s * 2.0 ** (retry_number - 1))
        jittered = backoff * rng.uniform(0.5, 1.0)
        return max(jittered, retry_after_s or 0.0)


def _retry_after_s(reply: HttpReply) -> float | None:
    value = reply.header(RETRY_AFTER_HEADER)
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # an HTTP-date; the jittered backoff applies instead


def _note_rate_headers(reply: HttpReply, limiter: TokenBucket) -> None:
    remaining = reply.header(REMAINING_REQUESTS_HEADER)
    if remaining is not None and remaining.strip() == "0":
        _log.warning("rate_limit_exhausted", status=reply.status)
        limiter.drain()


@dataclass(frozen=True)
class SendResult:
    """A successful reply plus how many attempts it took."""

    reply: HttpReply
    attempts: int


@dataclass(frozen=True)
class Sender:
    """Sends requests through a transport with rate limiting and retries.

    Attributes:
        transport: The injected transport (real HTTP, a replay-cache wrapper, or a test fake).
        limiter: Token bucket consulted before every attempt.
        policy: Backoff policy.
        sleep: Blocking sleep in seconds (injected in tests).
        rng: Random source for the jitter (seed it for deterministic tests).
    """

    transport: Transport
    limiter: TokenBucket
    policy: RetryPolicy
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.Random)

    def send(self, request: Mapping[str, object]) -> SendResult:
        """Send `request`, retrying 429, 5xx and transport errors up to the policy's limit.

        Args:
            request: The transport request mapping.

        Returns:
            The first 2xx reply and the number of attempts used.

        Raises:
            ModelHTTPError: On a non-retryable status, or when every attempt failed with a status.
            ModelTransportError: When every attempt failed without an HTTP answer.
        """
        for attempt in range(1, self.policy.max_attempts + 1):
            self.limiter.acquire()
            try:
                reply = parse_reply(self.transport(request))
            except ModelTransportError:
                if attempt == self.policy.max_attempts:
                    raise
                _log.warning("model_transport_retry", attempt=attempt, path=request.get("path"))
                self.sleep(self.policy.delay_s(attempt, self.rng, None))
                continue
            _note_rate_headers(reply, self.limiter)
            if reply.ok:
                return SendResult(reply=reply, attempts=attempt)
            if not reply.retryable or attempt == self.policy.max_attempts:
                msg = f"HTTP {reply.status} from {request.get('path')}: {reply.error_message()}"
                raise ModelHTTPError(msg, status=reply.status, attempts=attempt)
            _log.warning("model_http_retry", attempt=attempt, status=reply.status)
            self.sleep(self.policy.delay_s(attempt, self.rng, _retry_after_s(reply)))
        msg = "max_attempts must be at least 1"
        raise ValueError(msg)


class HttpxTransport:
    """The real transport: authenticated HTTPS calls with `httpx` (`Bearer` by default)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_s: float = 120.0,
        client: httpx.Client | None = None,
        auth_scheme: str = "Bearer",
    ) -> None:
        """Create the transport.

        Args:
            base_url: API root, e.g. the value of `META_API_BASE_URL`; paths are appended to it.
            api_key: Bearer token. Never logged.
            timeout_s: Per-request timeout in seconds.
            client: Optional preconfigured client (tests pass one with `httpx.MockTransport`).
            auth_scheme: The `Authorization` scheme, e.g. `Api-Key` for Baseten.
        """
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"{auth_scheme} {api_key}"}
        self._client = client or httpx.Client(timeout=timeout_s)

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        """Send one request and return `{status, headers, json}`.

        Args:
            request: A transport request mapping (see the module docstring).

        Returns:
            The reply mapping; `json` is `None` when the body is not JSON.

        Raises:
            ModelTransportError: If no HTTP answer was received.
        """
        path = str(request["path"])
        method = str(request.get("method", "POST"))
        try:
            response = self._client.request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers,
                json=request.get("json"),
                data=_form(request),
                files=_files(request),
            )
        except httpx.TransportError as exc:
            msg = f"{method} {path} failed: {type(exc).__name__}"
            raise ModelTransportError(msg) from exc
        try:
            body: object = response.json()
        except ValueError:
            body = None
        headers = {k.lower(): v for k, v in response.headers.items()}
        return {"status": response.status_code, "headers": headers, "json": body}


class PooledTransport:
    """Spreads requests over transports to identical servers, least busy first, with failover.

    Each request goes to the member with the fewest requests in flight (ties rotate). A member
    that gives no HTTP answer, a 404 (a proxy that lost its route) or a retryable status is
    passed over for the next one; when every member fails, the last reply is returned or the
    last error raised, so the caller's retry policy still sees it.
    """

    def __init__(self, members: Sequence[Transport]) -> None:
        """Create the pool.

        Args:
            members: One transport per server; at least one.

        Raises:
            ValueError: If `members` is empty.
        """
        if not members:
            msg = "a transport pool needs at least one member"
            raise ValueError(msg)
        self._members = list(members)
        self._in_flight = [0] * len(self._members)
        self._turn = 0
        self._lock = threading.Lock()

    def _order(self) -> list[int]:
        with self._lock:
            n = len(self._members)
            rotation = [(self._turn + i) % n for i in range(n)]
            self._turn = (self._turn + 1) % n
            return sorted(rotation, key=lambda i: self._in_flight[i])

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        """Send `request` to the least busy member, failing over to the others.

        Args:
            request: A transport request mapping.

        Returns:
            The first usable reply, else the last reply.

        Raises:
            ModelTransportError: If no member gave an HTTP answer.
        """
        last_reply: Mapping[str, object] | None = None
        last_error: ModelTransportError | None = None
        for index in self._order():
            with self._lock:
                self._in_flight[index] += 1
            try:
                reply = self._members[index](request)
            except ModelTransportError as exc:
                last_error = exc
                continue
            finally:
                with self._lock:
                    self._in_flight[index] -= 1
            parsed = parse_reply(reply)
            if parsed.status == HTTP_NOT_FOUND or parsed.retryable:
                last_reply = reply
                continue
            return reply
        if last_reply is not None or last_error is None:
            return last_reply or {"status": HTTP_NOT_FOUND, "headers": {}, "json": None}
        raise last_error


def _form(request: Mapping[str, object]) -> dict[str, str] | None:
    form = request.get("form")
    return {str(k): str(v) for k, v in form.items()} if isinstance(form, Mapping) else None


def _part(field: str, part: Mapping[str, object]) -> tuple[str, tuple[str | None, bytes, str]]:
    content = part["content"]
    if not isinstance(content, bytes):
        msg = f"multipart part {field!r} content must be bytes"
        raise TypeError(msg)
    filename = part.get("filename")
    return field, (
        str(filename) if filename is not None else None,
        content,
        str(part["content_type"]),
    )


def _files(request: Mapping[str, object]) -> list[tuple[str, tuple[str | None, bytes, str]]] | None:
    parts: list[tuple[str, tuple[str | None, bytes, str]]] = []
    named = request.get("parts")
    if isinstance(named, Mapping):
        parts.extend(_part(str(k), v) for k, v in named.items() if isinstance(v, Mapping))
    file = request.get("file")
    if isinstance(file, Mapping):
        parts.append(_part("file", file))  # last, as the Files API recommends
    return parts or None
