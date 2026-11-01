"""Anonymous tap disputes: abuse bounds, Spark Open re-review and chain records (task P2-11).

A dispute is chained as `pending` before anything else can fail, then re-reviewed by Tier 2
(`review_dispute`, P3-04; the policy sends every dispute to Tier 2, P3-05), and the outcome is
chained as a second `dispute` record with the same `dispute_id`. A review that cannot run leaves
it `pending` for a person: it is never dropped and never upheld by default. An upheld dispute
also chains a `false_alarm` rule-health `label` and, with `hard_negatives`, becomes a permanent
Rule CI lookalike regression (WS1f, `rule_ci.hard_negatives`).

**No identity is stored.** The rate limit keys an HMAC of the client address with a random salt
that is replaced, together with every counter, at each UTC day boundary; the salt is never
written anywhere, so yesterday's keys cannot be recomputed or linked to today's.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from datetime import date
from typing import Final, Protocol

from rulecam.contracts import CandidateEvent, Clock, Dispute, RuleIR, VerifierVerdict
from rulecam.errors import RulecamError
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.worker.errors import DisputeLimitError, UnknownEventError
from rulecam.worker.log import LoggedEvent, SiteLog, dispute_payload, read_site_log
from rulecam.worker.models import DisputeRequest, DisputeResult

_log = get_logger(__name__)

PER_CLIENT_PER_DAY: Final = 5
PER_SITE_PER_DAY: Final = 200
PER_EVENT: Final = 3
DISPUTE_WINDOW_DAYS: Final = 14
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]+")


class Transcriber(Protocol):
    """Voice transcriber protocol (e.g. Meta Voice Transcribe)."""

    def transcribe(
        self,
        audio: bytes,
        *,
        lang_bias: list[str] | None = None,
        keywords: list[str] | None = None,
        diarize: bool = False,
    ) -> list[dict[str, str]]:
        """Transcribe audio bytes to text segments."""
        ...


class DisputeReviewer(Protocol):
    """Tier 2 dispute re-review (`SparkOpenVerifier` or `VerificationPolicy`)."""

    def review_dispute(
        self,
        candidate: CandidateEvent,
        rule: RuleIR,
        dispute: Dispute,
        tier1: VerifierVerdict | None = None,
    ) -> Dispute:
        """Re-review a disputed alert."""
        ...


class FalseAlarmSink(Protocol):
    """Remembers a confirmed false alarm (`rule_ci.hard_negatives.HardNegatives`)."""

    def from_dispute(self, candidate: CandidateEvent, rule: RuleIR, dispute: Dispute) -> object:
        """Record an upheld dispute."""
        ...


class DailyLimiter:
    """Per-client and per-site daily caps keyed by a salted HMAC that rotates every UTC day."""

    def __init__(
        self,
        clock: Clock,
        *,
        per_client: int = PER_CLIENT_PER_DAY,
        per_day: int = PER_SITE_PER_DAY,
    ) -> None:
        """Create the limiter.

        Args:
            clock: Time source (the day boundary).
            per_client: Disputes per client address per day.
            per_day: Disputes per site per day (bounds Tier 2 spend and memory).
        """
        self._clock = clock
        self._per_client = per_client
        self._per_day = per_day
        self._lock = threading.Lock()
        self._day: date | None = None
        self._salt = b""
        self._counts: dict[str, int] = {}

    def allow(self, client: str) -> bool:
        """Count one attempt by `client` and return whether it is within both caps."""
        with self._lock:
            today = self._clock.now().date()
            if today != self._day:
                self._day, self._salt, self._counts = today, secrets.token_bytes(32), {}
            if sum(self._counts.values()) >= self._per_day:
                return False
            key = hmac.new(self._salt, client.encode(), hashlib.sha256).hexdigest()
            used = self._counts.get(key, 0)
            if used >= self._per_client:
                return False
            self._counts[key] = used + 1
            return True


def transcribe_and_discard_audio(
    audio: bytes | bytearray,
    transcriber: Transcriber,
    *,
    lang_hint: str | None = None,
) -> str:
    """Transcribe voice audio and immediately discard the audio buffer.

    Worker voice recordings are never stored to disk or kept in memory after transcription.
    """
    try:
        segments = transcriber.transcribe(
            bytes(audio),
            lang_bias=[lang_hint] if lang_hint else None,
        )
    finally:
        # Zero out if mutable, and drop reference immediately
        if isinstance(audio, bytearray):
            audio[:] = b"\x00" * len(audio)
        del audio

    words = " ".join(seg.get("text", "") for seg in segments).strip()
    return " ".join(_CONTROL.sub(" ", words).split())


def reason_text(request: DisputeRequest) -> str:
    """The dispute's `reason_text`: the choice, then the worker's words without control chars."""
    words = " ".join(_CONTROL.sub(" ", request.reason_text).split())
    return f"{request.reason_choice}: {words}" if words else request.reason_choice


class DisputeService:
    """Accepts tap disputes for one site."""

    def __init__(
        self,
        chain: EventChain,
        reviewer: DisputeReviewer,
        *,
        clock: Clock,
        limiter: DailyLimiter | None = None,
        hard_negatives: FalseAlarmSink | None = None,
    ) -> None:
        """Create the service.

        Args:
            chain: The site's evidence chain (events are read from it, disputes written to it).
            reviewer: Tier 2.
            clock: Time source.
            limiter: The abuse bounds (defaults to `DailyLimiter(clock)`).
            hard_negatives: Turns upheld disputes into permanent Rule CI regressions.
        """
        self._chain = chain
        self._reviewer = reviewer
        self._clock = clock
        self._limiter = limiter or DailyLimiter(clock)
        self._hard_negatives = hard_negatives

    def submit(self, request: DisputeRequest, client: str) -> DisputeResult:
        """Record, re-review and chain one dispute.

        Args:
            request: The worker's dispute.
            client: The client address, used only for the in-memory salted rate limit.

        Returns:
            The outcome and the rule's updated counts on the event's camera.

        Raises:
            DisputeLimitError: Over the per-client, per-site or per-event cap.
            UnknownEventError: No event of the last 14 days has this code.
        """
        if not self._limiter.allow(client):
            msg = "too many disputes today; try again tomorrow"
            raise DisputeLimitError(msg)
        log = read_site_log(self._chain, self._clock.now().date(), DISPUTE_WINDOW_DAYS)
        event = log.by_code(request.event_code)
        if event is None:
            msg = f"no recent event with code {request.event_code}"
            raise UnknownEventError(msg)
        if len(log.disputes_of(event.candidate.event_id)) >= PER_EVENT:
            msg = "this event has already been disputed the maximum number of times"
            raise DisputeLimitError(msg)
        dispute_id = f"dp-{secrets.token_hex(8)}"
        dispute = Dispute(channel="tap", reason_text=reason_text(request))
        self._chain.append("dispute", dispute_payload(dispute_id, event, dispute))
        reviewed = self._review(event, dispute)
        self._chain.append("dispute", dispute_payload(dispute_id, event, reviewed))
        if reviewed.outcome == "upheld":
            self._upheld(event, dispute_id, reviewed)
        _log.info(
            "worker_dispute",
            event_id=event.candidate.event_id,
            camera_id=event.candidate.camera_id,
            outcome=reviewed.outcome,
        )
        after = read_site_log(self._chain, self._clock.now().date(), DISPUTE_WINDOW_DAYS)
        return _result(request.event_code, event, reviewed, after)

    def submit_voice(
        self,
        event_code: str,
        audio: bytes,
        client: str,
        *,
        transcriber: Transcriber,
        lang_hint: str | None = None,
    ) -> DisputeResult:
        """Record, re-review and chain one voice dispute, discarding audio immediately.

        Args:
            event_code: The 8-character event code from the alert poster.
            audio: Raw audio bytes (transcribed and immediately destroyed).
            client: The client address for in-memory salted rate limiting.
            transcriber: Voice transcriber service.
            lang_hint: Optional language code bias (e.g. 'es').

        Returns:
            The outcome and updated counts.
        """
        if not self._limiter.allow(client):
            msg = "too many disputes today; try again tomorrow"
            raise DisputeLimitError(msg)
        log = read_site_log(self._chain, self._clock.now().date(), DISPUTE_WINDOW_DAYS)
        event = log.by_code(event_code)
        if event is None:
            msg = f"no recent event with code {event_code}"
            raise UnknownEventError(msg)
        if len(log.disputes_of(event.candidate.event_id)) >= PER_EVENT:
            msg = "this event has already been disputed the maximum number of times"
            raise DisputeLimitError(msg)

        transcript = transcribe_and_discard_audio(audio, transcriber, lang_hint=lang_hint)
        dispute_text = transcript if transcript else "voice dispute (unintelligible)"

        dispute_id = f"dp-{secrets.token_hex(8)}"
        dispute = Dispute(channel="voice", reason_text=dispute_text)
        self._chain.append("dispute", dispute_payload(dispute_id, event, dispute))
        reviewed = self._review(event, dispute)
        self._chain.append("dispute", dispute_payload(dispute_id, event, reviewed))
        if reviewed.outcome == "upheld":
            self._upheld(event, dispute_id, reviewed)
        _log.info(
            "worker_voice_dispute",
            event_id=event.candidate.event_id,
            camera_id=event.candidate.camera_id,
            outcome=reviewed.outcome,
        )
        after = read_site_log(self._chain, self._clock.now().date(), DISPUTE_WINDOW_DAYS)
        return _result(event_code, event, reviewed, after)

    def _upheld(self, event: LoggedEvent, dispute_id: str, reviewed: Dispute) -> None:
        """Label the false alarm for rule health and remember it as a regression."""
        self._chain.append("label", _label(event, dispute_id))
        if self._hard_negatives is not None:
            self._hard_negatives.from_dispute(event.candidate, event.rule, reviewed)

    def _review(self, event: LoggedEvent, dispute: Dispute) -> Dispute:
        """Tier 2 re-review; anything that stops it leaves the dispute pending for a person."""
        try:
            return self._reviewer.review_dispute(event.candidate, event.rule, dispute, event.tier1)
        except RulecamError as exc:
            _log.warning(
                "worker_dispute_review_failed",
                event_id=event.candidate.event_id,
                error=type(exc).__name__,
            )
            return dispute.model_copy(
                update={
                    "outcome": "pending",
                    "reviewer_reasoning": f"review failed: {type(exc).__name__}",
                }
            )


def _label(event: LoggedEvent, dispute_id: str) -> dict[str, object]:
    """The rule-health label of an upheld dispute."""
    return {
        "event_id": event.candidate.event_id,
        "rule_id": event.candidate.rule_id,
        "label": "false_alarm",
        "source": "worker_dispute",
        "dispute_id": dispute_id,
    }


def _result(code: str, event: LoggedEvent, reviewed: Dispute, log: SiteLog) -> DisputeResult:
    candidate = event.candidate
    return DisputeResult(
        code=code,
        rule_id=candidate.rule_id,
        outcome=reviewed.outcome,
        counts=log.counts(candidate.camera_id, candidate.rule_id),
    )
