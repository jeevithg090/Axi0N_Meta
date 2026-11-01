"""Ask Glimmer about a recorded candidate again, served only from recorded replies.

The runtime sent Glimmer the chained candidate with its strip attached (`EpisodeStrips.render`
fills `strip_paths`), so the same candidate with the same strip bytes builds the same request,
which has the same replay-cache key (the prompt names the frame count, never the paths). No
transport can reach the network here: a request that was not recorded raises `ReplayMiss`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from rulecam.clients.open_weights.glimmer import PROVIDER, GlimmerClient, GlimmerConfig
from rulecam.contracts import CandidateEvent, RuleIR, VerifierVerdict
from rulecam.replay.cache import ReplayCache
from rulecam.verify.tier1 import GlimmerVerifier

_RECORDED_AT = datetime(2026, 1, 1, tzinfo=UTC)


class _FixedClock:
    """Golden runs never stamp anything with the wall clock."""

    def now(self) -> datetime:
        return _RECORDED_AT


def _offline(request: Mapping[str, object]) -> Mapping[str, object]:
    """The transport behind the cache; replay mode never calls it."""
    msg = f"golden replays never reach the network ({request.get('path')})"
    raise AssertionError(msg)


def replay_cache(directory: Path) -> ReplayCache:
    """A replay-mode cache over `directory` (never records, never calls a model)."""
    return ReplayCache(directory, "replay", clock=_FixedClock())


def with_strip(candidate: CandidateEvent, strips: Sequence[Path]) -> CandidateEvent:
    """The candidate as the runtime verified it: the chained one with its strip attached."""
    return candidate.model_copy(update={"strip_paths": tuple(str(p) for p in strips)})


def replay_tier1(
    candidate: CandidateEvent, rule: RuleIR, *, model: str, cache: ReplayCache
) -> VerifierVerdict:
    """Return Glimmer's recorded verdict on `candidate` (with its strip attached).

    Args:
        candidate: The candidate with `strip_paths` pointing at byte-identical strip copies.
        rule: The rule that fired.
        model: The Glimmer model name the runtime used (the verdict's `model_version`).
        cache: A replay-mode cache holding the recorded reply.

    Returns:
        The verdict, with `latency_ms` 0 (golden outputs never depend on timing).

    Raises:
        ReplayMiss: If the request was not recorded.
        ModelClientError: If the recorded reply does not parse as a Tier 1 answer.
    """
    transport = cache.wrap_transport(PROVIDER, model, _offline)
    client = GlimmerClient(transport, clock=_FixedClock(), config=GlimmerConfig(model=model))
    verifier = GlimmerVerifier(client, monotonic=lambda: 0.0, raise_errors=True)
    return verifier.verify(candidate, rule)
