"""A failed Tier 1 verification is never silent: bounded retry and a circuit breaker (P3-05).

When Glimmer cannot answer (the GPU pod is stopped or unreachable, a timeout, a server error, a
reply that is not the schema twice, an unreadable strip) there is no verdict to decide on. The
runtime then records an **error verdict**: `unsure`, score 0.5, with a reason that starts
`verifier_error: ` (existing `VerifierVerdict` fields, no contract change). The policy routes
such a candidate to the human queue (`policy.decide_unverified`), whatever its severity, so a
person always sees it; Tier 2 may confirm it first when the rule allows it.

`GuardedVerifier` wraps the Tier 1 verifier:

- **Bounded retry.** A transient failure (no HTTP answer, a 429 or 5xx, a timeout) is retried
  `retries` times with exponential backoff, and only while the first attempts took less than
  `retry_budget_s`, so a hanging server is not waited on twice. A bad reply is not retried (the
  client already retried the schema once): the server is up, it just did not give a verdict.
- **Circuit breaker.** After `failure_threshold` transient failures in a row the circuit
  **opens**: verifications fail fast with an error verdict (no call, no timeout), so a dead
  Glimmer adds no latency per candidate and the queue does not back up. After `cooldown_s` one
  verification is let through (**half-open**); if it succeeds the circuit closes, if not it opens
  again for twice as long (at most `max_cooldown_s`). While open, `tick` (the API's once-a-second
  ticker) also probes Glimmer's health endpoint every `probe_interval_s`; a healthy answer moves
  the circuit to half-open, so the next candidate checks it for real.
- **Health.** `health()` reports the state; `down` is true until the circuit closes again. It is
  served in `/runtime/metrics` (`verifier`, `verifier_down`), exported to Prometheus and shown
  as a red badge in the UI header.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

import httpx

from rulecam.clients.errors import ModelHTTPError
from rulecam.clients.transport import ModelTransportError
from rulecam.contracts import CandidateEvent, Frozen, RuleIR, Verifier, VerifierVerdict
from rulecam.log import get_logger

_log = get_logger(__name__)

VERIFIER_ERROR = "verifier_error"
ERROR_PREFIX = f"{VERIFIER_ERROR}: "
ERROR_SCORE = 0.5
UNKNOWN_MODEL = "unavailable"
MAX_DETAIL = 160
HTTP_TOO_MANY = 429
HTTP_SERVER_ERROR = 500
HTTP_OK = 200
PROBE_TIMEOUT_S = 2.0

BreakerState = Literal["closed", "open", "half_open"]


def error_verdict(
    reason: str, *, latency_ms: int = 0, model_version: str = UNKNOWN_MODEL
) -> VerifierVerdict:
    """The Tier 1 verdict recorded when there is no real one (`unsure`, `verifier_error: …`).

    Args:
        reason: Why verification failed, in one line.
        latency_ms: Time spent trying.
        model_version: The Tier 1 model, when known.

    Returns:
        An `unsure` Glimmer verdict whose reason starts `verifier_error: `.
    """
    return VerifierVerdict(
        tier="glimmer",
        verdict="unsure",
        score=ERROR_SCORE,
        reason=ERROR_PREFIX + reason,
        latency_ms=max(0, latency_ms),
        model_version=model_version,
    )


def verifier_error(verdict: VerifierVerdict) -> str | None:
    """Why verification failed, if `verdict` is an error verdict; `None` for a real verdict."""
    if verdict.verdict != "unsure" or not verdict.reason.startswith(ERROR_PREFIX):
        return None
    return verdict.reason.removeprefix(ERROR_PREFIX) or VERIFIER_ERROR


def describe(exc: BaseException) -> str:
    """One line for an exception: its type and the start of its message."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    name = type(exc).__name__
    return f"{name}: {text[:MAX_DETAIL]}" if text else name


def is_transient(exc: BaseException) -> bool:
    """Whether a failure says the verifier is unreachable or overloaded (worth a retry).

    No HTTP answer, a timeout or a broken connection, a 429 or a 5xx are transient. A bad reply
    (the schema twice), an unreadable strip or a replay miss are not: retrying would not help,
    and the server (if any) did answer.
    """
    if isinstance(exc, ModelHTTPError):
        return exc.status == HTTP_TOO_MANY or exc.status >= HTTP_SERVER_ERROR
    return isinstance(exc, ModelTransportError | TimeoutError | ConnectionError | httpx.HTTPError)


class VerifierHealth(Frozen):
    """The Tier 1 circuit, served in `/runtime/metrics` → `verifier`.

    Attributes:
        state: `closed` (calls go through), `open` (fail fast) or `half_open` (one trial).
        down: The verifier is not known to be up (any state but `closed`).
        consecutive_failures: Transient failures in a row.
        failures: Verifications that ended without a verdict (after retries).
        fast_failures: Verifications refused while the circuit was open.
        opens: How many times the circuit opened.
        last_error: The last failure, in one line.
        retry_in_s: Seconds until the next trial (0 unless open).
    """

    state: BreakerState = "closed"
    down: bool = False
    consecutive_failures: int = 0
    failures: int = 0
    fast_failures: int = 0
    opens: int = 0
    last_error: str = ""
    retry_in_s: float = 0.0


@dataclass(frozen=True)
class BreakerPolicy:
    """Retry and circuit-breaker settings.

    Attributes:
        retries: Extra attempts after a transient failure.
        backoff_s: Wait before the first retry; doubles on each further retry.
        retry_budget_s: No retry once the attempts so far took this long.
        failure_threshold: Transient failures in a row that open the circuit.
        cooldown_s: How long the circuit stays open the first time.
        max_cooldown_s: The longest it stays open (the cooldown doubles on each failed trial).
        probe_interval_s: How often `tick` probes the health endpoint while open.
    """

    retries: int = 1
    backoff_s: float = 1.0
    retry_budget_s: float = 10.0
    failure_threshold: int = 3
    cooldown_s: float = 30.0
    max_cooldown_s: float = 300.0
    probe_interval_s: float = 10.0


class CircuitBreaker:
    """Closed → open after repeated failures → half-open after a cooldown → closed (thread-safe)."""

    def __init__(
        self,
        policy: BreakerPolicy | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a closed circuit.

        Args:
            policy: Thresholds and cooldowns.
            monotonic: Seconds (injected in tests).
        """
        self.policy = policy or BreakerPolicy()
        self._now = monotonic
        self._lock = threading.Lock()
        self._state: BreakerState = "closed"
        self._consecutive = 0
        self._failures = 0
        self._fast = 0
        self._opens = 0
        self._last_error = ""
        self._cooldown = self.policy.cooldown_s
        self._reopen_at = 0.0
        self._trial = False
        self._next_probe = 0.0

    @property
    def state(self) -> BreakerState:
        """The current state."""
        with self._lock:
            return self._state

    def allow(self) -> bool:
        """Whether a call may go out now (counts a fast failure when it may not)."""
        with self._lock:
            if self._state == "open" and self._now() >= self._reopen_at:
                self._state = "half_open"
                _log.info("verifier_circuit_half_open", reason="cooldown elapsed")
            if self._state == "closed":
                return True
            if self._state == "half_open" and not self._trial:
                self._trial = True
                return True
            self._fast += 1
            return False

    def succeeded(self) -> None:
        """A call got an answer from the verifier: close the circuit."""
        with self._lock:
            if self._state != "closed":
                _log.warning("verifier_circuit_closed", after_opens=self._opens)
            self._state, self._consecutive, self._trial = "closed", 0, False
            self._cooldown = self.policy.cooldown_s

    def failed(self, error: str) -> None:
        """A call ended without a verdict for a transient reason; open the circuit if due."""
        with self._lock:
            self._failures += 1
            self._consecutive += 1
            self._last_error = error
            trial, self._trial = self._trial, False
            if trial:  # the half-open trial failed: open again, for longer
                self._cooldown = min(self._cooldown * 2, self.policy.max_cooldown_s)
                self._open()
            elif self._state == "closed" and self._consecutive >= self.policy.failure_threshold:
                self._open()

    def answered_badly(self, error: str) -> None:
        """A call reached the verifier but got no usable verdict (it is up; nothing opens)."""
        with self._lock:
            self._failures += 1
            self._last_error = error
        self.succeeded()

    def _open(self) -> None:
        self._state = "open"
        self._opens += 1
        now = self._now()
        self._reopen_at = now + self._cooldown
        self._next_probe = now + self.policy.probe_interval_s
        _log.error(
            "verifier_circuit_open",
            consecutive_failures=self._consecutive,
            cooldown_s=self._cooldown,
            last_error=self._last_error,
            behaviour="candidates go to the human queue until the verifier answers again",
        )

    def probe_due(self) -> bool:
        """Whether `tick` should probe the verifier's health now (open, and the interval passed)."""
        with self._lock:
            if self._state != "open" or self._now() < self._next_probe:
                return False
            self._next_probe = self._now() + self.policy.probe_interval_s
            return True

    def probe_succeeded(self) -> None:
        """The health endpoint answered: let the next verification through as the trial."""
        with self._lock:
            if self._state == "open":
                self._state, self._trial = "half_open", False
                _log.info("verifier_circuit_half_open", reason="health probe answered")

    def health(self) -> VerifierHealth:
        """The circuit's state for `/runtime/metrics`."""
        with self._lock:
            retry = max(0.0, self._reopen_at - self._now()) if self._state == "open" else 0.0
            return VerifierHealth(
                state=self._state,
                down=self._state != "closed",
                consecutive_failures=self._consecutive,
                failures=self._failures,
                fast_failures=self._fast,
                opens=self._opens,
                last_error=self._last_error,
                retry_in_s=round(retry, 1),
            )


def http_probe(
    url: str,
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    headers: Mapping[str, str] | None = None,
) -> Callable[[], bool]:
    """A health probe: `GET url` answers 200 within `timeout_s` (llama-server's `/health`).

    `headers` carries a key for a guarded URL, e.g. a hosted API's `GET /models`.
    """

    def probe() -> bool:
        try:
            return httpx.get(url, headers=headers, timeout=timeout_s).status_code == HTTP_OK
        except httpx.HTTPError:
            return False

    return probe


class GuardedVerifier:
    """A Tier 1 `Verifier` that retries, fails fast while its circuit is open, and never raises.

    Every failure becomes an error verdict (`error_verdict`), which the policy sends to a person.
    """

    def __init__(
        self,
        inner: Verifier,
        *,
        model_version: str = UNKNOWN_MODEL,
        breaker: CircuitBreaker | None = None,
        probe: Callable[[], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Wrap `inner`.

        Args:
            inner: The verifier; it raises when it cannot verify (`GlimmerVerifier` with
                `raise_errors=True`).
            model_version: Recorded on error verdicts.
            breaker: The circuit (default: `BreakerPolicy()` on `monotonic`).
            probe: Health check run by `tick` while the circuit is open; `None` relies on the
                half-open trial alone.
            sleep: Backoff wait (injected in tests).
            monotonic: Seconds (injected in tests).
        """
        self._inner = inner
        self._model = model_version
        self._breaker = breaker or CircuitBreaker(monotonic=monotonic)
        self._probe = probe
        self._sleep = sleep
        self._now = monotonic

    @property
    def breaker(self) -> CircuitBreaker:
        """The circuit."""
        return self._breaker

    def health(self) -> VerifierHealth:
        """The circuit's state (`/runtime/metrics` → `verifier`)."""
        return self._breaker.health()

    def verify(self, candidate: CandidateEvent, rule: RuleIR) -> VerifierVerdict:
        """Verify with retry; on failure, or while the circuit is open, return an error verdict.

        Args:
            candidate: The candidate with its strip.
            rule: Its rule.

        Returns:
            The verifier's verdict, or an `unsure` error verdict (`verifier_error: …`).
        """
        if not self._breaker.allow():
            health = self._breaker.health()
            reason = f"circuit open, failing fast (last error: {health.last_error or 'unknown'})"
            return error_verdict(reason, model_version=self._model)
        started = self._now()
        error = VERIFIER_ERROR
        for attempt in range(self._breaker.policy.retries + 1):
            try:
                verdict = self._inner.verify(candidate, rule)
            except Exception as exc:  # any failure means no verdict; it is decided, never dropped
                error = describe(exc)
                if not is_transient(exc):
                    self._breaker.answered_badly(error)
                    break
                if not self._retry(attempt, started):
                    self._breaker.failed(error)
                    break
                _log.warning("tier1_retry", event_id=candidate.event_id, error=error)
                self._sleep(self._breaker.policy.backoff_s * 2.0**attempt)
            else:
                self._breaker.succeeded()
                return verdict
        latency_ms = round((self._now() - started) * 1000)
        _log.warning("tier1_failed", event_id=candidate.event_id, error=error)
        return error_verdict(error, latency_ms=latency_ms, model_version=self._model)

    def _retry(self, attempt: int, started: float) -> bool:
        policy = self._breaker.policy
        within = self._now() - started < policy.retry_budget_s
        return attempt < policy.retries and within and self._breaker.state == "closed"

    def tick(self) -> None:
        """Probe the health endpoint while the circuit is open (the API calls this every second)."""
        if self._probe is None or not self._breaker.probe_due():
            return
        if self._probe():
            self._breaker.probe_succeeded()
        else:
            _log.info("verifier_probe_failed", health=self._breaker.health().last_error)
