"""`rulecam audit`: verify a chain, then re-derive every candidate from the track store (E9).

The chain proves the records were not edited; re-derivation proves each recorded decision is what
the rules engine (a pure function) gives on the stored tracks. Together they back the claim
"N/N decisions reproduced".
"""

from __future__ import annotations

from pathlib import Path

from rulecam.contracts import Frozen, TrackStore
from rulecam.evidence.chain import ChainReport, read_chain, verify_chain
from rulecam.rules.backtest import rederive


class AuditReport(Frozen):
    """The audit result for one chain file.

    Attributes:
        chain: Integrity of the chain itself.
        candidates: Candidate records found.
        reproduced: Candidates the engine reproduced exactly.
        failed_seqs: Seqs of candidates that did not reproduce.
    """

    chain: ChainReport
    candidates: int
    reproduced: int
    failed_seqs: tuple[int, ...] = ()

    @property
    def ok(self) -> bool:
        """Chain intact and every decision reproduced."""
        return self.chain.ok and self.reproduced == self.candidates

    def summary(self) -> str:
        """One line for the CLI and the PR evidence."""
        integrity = (
            f"chain intact ({self.chain.n} records)"
            if self.chain.ok
            else f"chain broken at event {self.chain.first_bad_seq}"
        )
        return f"{integrity}; {self.reproduced}/{self.candidates} decisions reproduced"


def audit_chain(path: Path, store: TrackStore) -> AuditReport:
    """Verify `path` and re-derive its candidates.

    A broken chain is reported without re-deriving: its records cannot be trusted.

    Args:
        path: A chain JSONL file.
        store: The track store the candidates were evaluated on.

    Returns:
        The audit report.
    """
    chain = verify_chain(path)
    if not chain.ok:
        return AuditReport(chain=chain, candidates=0, reproduced=0)
    candidates = [r for r in read_chain(path) if r.kind == "candidate"]
    failed = tuple(r.seq for r in candidates if not rederive(r, store))
    return AuditReport(
        chain=chain,
        candidates=len(candidates),
        reproduced=len(candidates) - len(failed),
        failed_seqs=failed,
    )
