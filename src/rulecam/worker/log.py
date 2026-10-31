"""Read a site's recent events and disputes back out of the evidence chain (task P2-11).

The chain is the only store: candidates (written by the runtime loop with their decision
context), Tier 1 verdicts and the `dispute` records written here. Public counts and event cards
are derived from it on every request, so they can always be re-checked against the chain.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from pydantic import ValidationError

from rulecam.contracts import CandidateEvent, Dispute, EventRecord, RuleIR, VerifierVerdict
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.log import get_logger
from rulecam.rules.backtest import read_candidate_payload
from rulecam.rules.errors import RuleEvaluationError
from rulecam.worker.models import DisputeOutcome, RuleCounts

_log = get_logger(__name__)

CODE_LENGTH: Final = 8


def event_code(event_id: str) -> str:
    """Return the short public code of an event (8 hex digits, not the event id itself)."""
    digest = hashlib.sha256(f"worker-card|{event_id}".encode()).hexdigest()
    return digest[:CODE_LENGTH].upper()


@dataclass(frozen=True)
class LoggedEvent:
    """A candidate from the chain, with what a dispute re-review needs."""

    candidate: CandidateEvent
    rule: RuleIR
    tier1: VerifierVerdict | None = None


@dataclass(frozen=True)
class LoggedDispute:
    """The latest state of one dispute."""

    dispute_id: str
    event_id: str
    outcome: DisputeOutcome


@dataclass(frozen=True)
class SiteLog:
    """A site's recent events (by event id) and disputes (by dispute id, latest record wins)."""

    events: Mapping[str, LoggedEvent]
    disputes: Mapping[str, LoggedDispute]

    def by_code(self, code: str) -> LoggedEvent | None:
        """Return the event with this short code, or `None`."""
        return next((e for i, e in self.events.items() if event_code(i) == code), None)

    def disputes_of(self, event_id: str) -> list[LoggedDispute]:
        """Return the event's disputes, oldest first."""
        return [d for d in self.disputes.values() if d.event_id == event_id]

    def counts(self, camera_id: str, rule_id: str) -> RuleCounts:
        """Return the public counts of one rule on one camera."""
        ids = {
            i
            for i, e in self.events.items()
            if e.candidate.camera_id == camera_id and e.candidate.rule_id == rule_id
        }
        disputes = [d for d in self.disputes.values() if d.event_id in ids]
        return RuleCounts(
            alerts=len(ids),
            disputes=len(disputes),
            upheld=sum(d.outcome == "upheld" for d in disputes),
        )


def dispute_payload(dispute_id: str, event: LoggedEvent, dispute: Dispute) -> dict[str, object]:
    """The chain payload of a `dispute` record (no identity: no IP, user agent or name)."""
    return {
        "dispute_id": dispute_id,
        "event_id": event.candidate.event_id,
        "rule_id": event.candidate.rule_id,
        "camera_id": event.candidate.camera_id,
        "dispute": dispute.model_dump(mode="json"),
    }


def read_site_log(chain: EventChain, today: date, days: int) -> SiteLog:
    """Read the last `days` days of the site's chain (today included).

    Args:
        chain: The site's chain.
        today: Today's date (UTC).
        days: How many days to read (the retention window).

    Returns:
        The events and disputes found. Records that cannot be parsed are logged and skipped.
    """
    records: list[EventRecord] = []
    for back in range(max(days, 1) - 1, -1, -1):
        path = chain.path_for(today - timedelta(days=back))
        if path.is_file():
            records.extend(read_chain(path))
    return _fold(records)


def _fold(records: Iterable[EventRecord]) -> SiteLog:
    events: dict[str, LoggedEvent] = {}
    tier1: dict[str, VerifierVerdict] = {}
    disputes: dict[str, LoggedDispute] = {}
    for record in records:
        try:
            if record.kind == "candidate":
                candidate, context = read_candidate_payload(record)
                events.setdefault(candidate.event_id, LoggedEvent(candidate, context.rule))
            elif record.kind == "verdict" and record.payload.get("tier") == "glimmer":
                body = {k: v for k, v in record.payload.items() if k != "event_id"}
                tier1[str(record.payload["event_id"])] = VerifierVerdict.model_validate(body)
            elif record.kind == "dispute":
                found = _dispute(record.payload)
                disputes[found.dispute_id] = found
        except (RuleEvaluationError, ValidationError, KeyError) as exc:
            _log.warning(
                "worker_chain_record_skipped", seq=record.seq, kind=record.kind, error=str(exc)
            )
    with_tier1 = {i: LoggedEvent(e.candidate, e.rule, tier1.get(i)) for i, e in events.items()}
    return SiteLog(events=with_tier1, disputes=disputes)


def _dispute(payload: Mapping[str, object]) -> LoggedDispute:
    dispute = Dispute.model_validate(payload["dispute"])
    return LoggedDispute(
        dispute_id=str(payload["dispute_id"]),
        event_id=str(payload["event_id"]),
        outcome=dispute.outcome,
    )
