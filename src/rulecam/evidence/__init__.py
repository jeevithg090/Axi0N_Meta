"""Tamper-evident evidence: the hash-chained log (P3-07a); roots, verify page, packs (P3-07b)."""

from rulecam.evidence.chain import (
    GENESIS_HASH,
    ChainReport,
    EventChain,
    canonical_json,
    js_number,
    read_chain,
    record_hash,
    verify_chain,
)
from rulecam.evidence.errors import ChainError, EvidenceError

__all__ = [
    "GENESIS_HASH",
    "ChainError",
    "ChainReport",
    "EventChain",
    "EvidenceError",
    "canonical_json",
    "js_number",
    "read_chain",
    "record_hash",
    "verify_chain",
]
