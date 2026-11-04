"""Tier 1 retry, circuit breaker and error verdicts (P3-05: a failed verify is never silent)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from rulecam.clients.errors import ModelHTTPError, StructuredOutputError
from rulecam.clients.transport import ModelTransportError
from rulecam.contracts import CandidateEvent, RuleIR, VerifierVerdict
from rulecam.replay.errors import ReplayMiss
from rulecam.verify.guard import (
    BreakerPolicy,
    CircuitBreaker,
    GuardedVerifier,
    describe,
    error_verdict,
    http_probe,
    is_transient,
    verifier_error,
)
from rulecam.verify.tier1 import GlimmerVerifier
from tests.fakes import make_verdict
from tests.verify.helpers import load_rule, make_candidate

DOWN = ModelTransportError("POST /chat/completions failed: ConnectError")


class Clock:
    """Monotonic seconds that only move when told to."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Flaky:
    """A Tier 1 verifier that raises the queued errors, then answers `violation`."""

    def __init__(self, *errors: Exception, clock: Clock | None = None, takes_s: float = 0.0):
        self.errors = list(errors)
        self.calls = 0
        self.clock = clock
        self.takes_s = takes_s

    def verify(self, candidate: CandidateEvent, rule: RuleIR) -> VerifierVerdict:
        self.calls += 1
        if self.clock is not None:
            self.clock.advance(self.takes_s)
        if self.errors:
            raise self.errors.pop(0)
        return make_verdict()


class Dead(Flaky):
    """Never answers."""

    def verify(self, candidate: CandidateEvent, rule: RuleIR) -> VerifierVerdict:
        self.calls += 1
        raise DOWN


def guarded(
    inner: Flaky,
    clock: Clock,
    *,
    probe: Callable[[], bool] | None = None,
    **policy: float,
) -> tuple[GuardedVerifier, list[float]]:
    sleeps: list[float] = []
    breaker = CircuitBreaker(BreakerPolicy(**policy), monotonic=clock)  # type: ignore[arg-type]
    verifier = GuardedVerifier(
        inner,
        model_version="muse-glimmer-30b",
        breaker=breaker,
        probe=probe,
        sleep=sleeps.append,
        monotonic=clock,
    )
    return verifier, sleeps


def verify(verifier: GuardedVerifier) -> VerifierVerdict:
    return verifier.verify(make_candidate(), load_rule())


# --- error verdicts ---------------------------------------------------------------------------


def test_error_verdict_is_unsure_with_a_verifier_error_reason() -> None:
    verdict = error_verdict("ConnectError", latency_ms=-3, model_version="m")
    assert (verdict.tier, verdict.verdict, verdict.score) == ("glimmer", "unsure", 0.5)
    assert verdict.reason == "verifier_error: ConnectError"
    assert verdict.latency_ms == 0
    assert verifier_error(verdict) == "ConnectError"
    assert verifier_error(error_verdict("")) == "verifier_error"


def test_a_real_unsure_verdict_is_not_an_error() -> None:
    assert verifier_error(make_verdict(verdict="unsure", score=0.5)) is None
    tricky = make_verdict(verdict="violation").model_copy(update={"reason": "verifier_error: x"})
    assert verifier_error(tricky) is None  # only an unsure verdict can be an error verdict


def test_describe_keeps_the_type_and_first_line() -> None:
    assert describe(RuntimeError("boom\nsecond line")) == "RuntimeError: boom"
    assert describe(TimeoutError()) == "TimeoutError"
    assert len(describe(ValueError("x" * 500))) < 200


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (DOWN, True),
        (ModelHTTPError("bad gateway", status=502, attempts=2), True),
        (ModelHTTPError("slow down", status=429, attempts=2), True),
        (ModelHTTPError("no such model", status=404, attempts=1), False),
        (TimeoutError("read"), True),
        (ConnectionRefusedError("refused"), True),
        (httpx.ConnectTimeout("t"), True),
        (StructuredOutputError("malformed JSON twice"), False),
        (ValueError("unreadable strip"), False),
        (FileNotFoundError("f0.png"), False),
        (ReplayMiss(provider="glimmer", model="m", key="k"), False),
    ],
)
def test_transient_failures(error: Exception, transient: bool) -> None:
    assert is_transient(error) is transient


# --- retry ------------------------------------------------------------------------------------


def test_a_transient_failure_is_retried_with_backoff() -> None:
    clock = Clock()
    inner = Flaky(DOWN)
    verifier, sleeps = guarded(inner, clock, backoff_s=2.0)
    verdict = verify(verifier)
    assert verdict.verdict == "violation"
    assert (inner.calls, sleeps) == (2, [2.0])
    assert verifier.health().failures == 0


def test_glimmer_raising_gives_an_error_verdict_after_retries() -> None:
    clock = Clock()
    inner = Flaky(DOWN, DOWN, clock=clock, takes_s=0.5)
    verifier, sleeps = guarded(inner, clock, retries=1)
    verdict = verify(verifier)
    assert verifier_error(verdict) == describe(DOWN)
    assert verdict.model_version == "muse-glimmer-30b"
    assert verdict.latency_ms == 1000
    assert (inner.calls, len(sleeps)) == (2, 1)
    health = verifier.health()
    assert (health.state, health.failures, health.consecutive_failures) == ("closed", 1, 1)
    assert health.last_error == describe(DOWN)


def test_a_slow_failure_is_not_retried() -> None:
    clock = Clock()
    inner = Flaky(TimeoutError("read timeout"), clock=clock, takes_s=60.0)
    verifier, sleeps = guarded(inner, clock, retry_budget_s=10.0)
    assert verifier_error(verify(verifier)) == "TimeoutError: read timeout"
    assert (inner.calls, sleeps) == (1, [])


def test_a_bad_reply_is_not_retried_and_does_not_trip_the_breaker() -> None:
    clock = Clock()
    inner = Flaky(*(StructuredOutputError("malformed JSON twice") for _ in range(5)))
    verifier, sleeps = guarded(inner, clock, failure_threshold=2)
    for _ in range(4):
        assert verifier_error(verify(verifier)) is not None
    assert (inner.calls, sleeps) == (4, [])
    health = verifier.health()
    assert (health.state, health.failures, health.consecutive_failures) == ("closed", 4, 0)


def test_the_real_glimmer_verifier_raises_for_the_guard() -> None:
    class DownClient:
        model = "muse-glimmer-30b"

        def structured(self, **kwargs: object) -> object:
            raise DOWN

    inner = GlimmerVerifier(DownClient(), raise_errors=True)  # type: ignore[arg-type]
    assert inner.model_version == "muse-glimmer-30b"
    with pytest.raises(ModelTransportError):
        inner.verify(make_candidate(), load_rule())
    verifier = GuardedVerifier(inner, model_version=inner.model_version, sleep=lambda s: None)
    verdict = verifier.verify(make_candidate(), load_rule())
    assert verdict.reason.startswith("verifier_error: ModelTransportError")
    # Without `raise_errors` it still answers `unsure` itself (other callers, e.g. the eval).
    fallback = GlimmerVerifier(DownClient()).verify(make_candidate(), load_rule())  # type: ignore[arg-type]
    assert (fallback.verdict, verifier_error(fallback)) == ("unsure", None)


# --- circuit breaker --------------------------------------------------------------------------


def test_the_breaker_opens_after_repeated_failures_and_fails_fast() -> None:
    clock = Clock()
    inner = Dead()
    verifier, _ = guarded(inner, clock, retries=0, failure_threshold=3, cooldown_s=30.0)
    for _ in range(3):
        verify(verifier)
    assert inner.calls == 3
    health = verifier.health()
    assert (health.state, health.down, health.opens, health.retry_in_s) == ("open", True, 1, 30.0)
    fast = verify(verifier)
    assert inner.calls == 3  # no call while open: no timeout per candidate
    assert fast.latency_ms == 0
    assert "circuit open" in fast.reason
    assert describe(DOWN) in fast.reason
    assert verifier.health().fast_failures == 1


def test_the_breaker_half_opens_after_the_cooldown_and_closes_on_success() -> None:
    clock = Clock()
    inner = Flaky(DOWN, DOWN)
    verifier, _ = guarded(inner, clock, retries=0, failure_threshold=2, cooldown_s=30.0)
    verify(verifier)
    verify(verifier)
    assert verifier.breaker.state == "open"
    clock.advance(29.0)
    assert verifier_error(verify(verifier)) is not None
    assert inner.calls == 2
    clock.advance(1.0)
    assert verify(verifier).verdict == "violation"  # the half-open trial got through
    health = verifier.health()
    assert (health.state, health.down, health.consecutive_failures) == ("closed", False, 0)


def test_a_failed_trial_reopens_for_twice_as_long() -> None:
    clock = Clock()
    inner = Dead()
    verifier, sleeps = guarded(
        inner, clock, retries=1, failure_threshold=1, cooldown_s=30.0, max_cooldown_s=45.0
    )
    verify(verifier)  # one failure (retried once) opens it
    assert (inner.calls, verifier.breaker.state) == (2, "open")
    clock.advance(30.0)
    verify(verifier)  # the trial: no retry while not closed
    assert (inner.calls, len(sleeps)) == (3, 1)
    health = verifier.health()
    assert (health.state, health.opens, health.retry_in_s) == ("open", 2, 45.0)  # capped
    clock.advance(45.0)
    verify(verifier)
    assert verifier.health().retry_in_s == 45.0


def test_only_one_trial_goes_out_while_half_open() -> None:
    breaker = CircuitBreaker(BreakerPolicy(failure_threshold=1, cooldown_s=1.0), monotonic=Clock())
    breaker.failed("down")
    breaker.probe_succeeded()
    assert breaker.state == "half_open"
    assert breaker.allow()  # the trial
    assert not breaker.allow()  # others fail fast meanwhile
    breaker.succeeded()
    assert breaker.allow()


def test_tick_probes_health_while_open() -> None:
    clock = Clock()
    answers = [False, True]
    probes: list[bool] = []

    def probe() -> bool:
        probes.append(answers[len(probes)])
        return probes[-1]

    inner = Flaky(DOWN)
    verifier, _ = guarded(
        inner,
        clock,
        probe=probe,
        retries=0,
        failure_threshold=1,
        cooldown_s=300.0,
        probe_interval_s=10.0,
    )
    verifier.tick()
    assert probes == []  # closed: nothing to probe
    verify(verifier)
    verifier.tick()
    assert probes == []  # not due yet
    clock.advance(10.0)
    verifier.tick()
    assert (probes, verifier.breaker.state) == ([False], "open")
    verifier.tick()
    assert probes == [False]  # the interval starts again
    clock.advance(10.0)
    verifier.tick()
    assert (probes, verifier.breaker.state) == ([False, True], "half_open")
    assert verifier.health().down  # down until a real verdict comes back
    assert verify(verifier).verdict == "violation"
    assert verifier.breaker.state == "closed"


def test_http_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[object] = [httpx.Response(200), httpx.Response(503), httpx.ConnectError("no")]
    urls: list[str] = []

    def get(url: str, *, headers: object = None, timeout: float) -> httpx.Response:
        urls.append(url)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, httpx.Response)
        return reply

    monkeypatch.setattr(httpx, "get", get)
    probe = http_probe("http://glimmer:8080/health")
    assert [probe(), probe(), probe()] == [True, False, False]
    assert urls == ["http://glimmer:8080/health"] * 3


def test_http_probe_sends_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[object] = []

    def get(url: str, *, headers: object = None, timeout: float) -> httpx.Response:
        sent.append(headers)
        return httpx.Response(200)

    monkeypatch.setattr(httpx, "get", get)
    assert http_probe("https://api.example/v1/models", headers={"Authorization": "Bearer k"})()
    assert sent == [{"Authorization": "Bearer k"}]
