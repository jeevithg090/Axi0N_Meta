"""Runner E9: does the evidence chain re-derive? (task P4-07, MP §8 E9).

E9 runs the audit (`rulecam.evidence.audit`) over the chain files of a run and reports two
separate things that are easy to confuse:

* **Chain integrity** — the hash chain is intact and nothing was inserted, reordered or edited.
* **Decision reproduction** — replaying each candidate's stored window through the rule engine
  gives the same decision. This is the stronger claim: it says the alert followed from the
  tracks, not just that the record was not tampered with.

A broken chain is reported without re-deriving anything, because records that cannot be trusted
cannot be evidence. So a file with a broken chain contributes to the integrity figure and is
left out of the reproduction figure, which E9 states rather than averaging the two together.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from eval.runners.common import Row, RunnerResult, not_measured, percent

from rulecam.evidence.audit import AuditReport

TITLE: Final = "Does the evidence chain re-derive?"
MAX_LISTED: Final = 20


def aggregate_e9(reports: Sequence[AuditReport], *, files: Sequence[str] = ()) -> RunnerResult:
    """Aggregate audit reports into the E9 table.

    Args:
        reports: One report per chain file, from `audit_chain`.
        files: The file names, in the same order, for naming a broken one.

    Returns:
        The result. With no reports, a "not measured" result.
    """
    if not reports:
        return not_measured(
            "e9", TITLE, "no chain file was audited. Run `rulecam audit` over a day's chain."
        )

    names = list(files) + [f"file {i}" for i in range(len(files), len(reports))]
    intact = [r for r in reports if r.chain.ok]
    broken = [names[i] for i, r in enumerate(reports) if not r.chain.ok]
    candidates = sum(r.candidates for r in intact)
    reproduced = sum(r.reproduced for r in intact)

    rows = [
        Row(
            name="Chain files intact",
            value=percent(len(intact), len(reports)),
            detail="the hash chain verifies end to end",
        ),
        Row(
            name="Decisions reproduced",
            value=percent(reproduced, candidates),
            detail=f"over the {len(intact)} intact file(s); a broken chain is not re-derived",
        ),
    ]

    caveats: list[str] = []
    if broken:
        caveats.append(
            f"**Chain broken in: {', '.join(broken)}.** Those files' candidates are excluded "
            "from the reproduction figure, because records that cannot be trusted are not "
            "evidence. Fix the chain before reading any number from them."
        )
    failed = [
        f"{names[i]}#{seq}" for i, r in enumerate(reports) for seq in r.failed_seqs if r.chain.ok
    ]
    if failed:
        caveats.append(
            f"Did not reproduce: {', '.join(failed[:MAX_LISTED])}"
            + (f" and {len(failed) - MAX_LISTED} more" if len(failed) > MAX_LISTED else "")
            + ". A candidate that does not reproduce means the stored window no longer gives "
            "that decision, which is a bug in the record or in the engine, not a rounding issue."
        )
    if candidates == 0:
        caveats.append(
            "The intact files hold no candidate, so reproduction is not measured: an empty "
            "chain re-derives trivially."
        )
    caveats.append(
        "Reproduction uses the track store as it is now. It shows the decision follows from the "
        "stored tracks; it cannot show the tracks match what the camera saw."
    )
    return RunnerResult(id="e9", title=TITLE, rows=tuple(rows), caveats=tuple(caveats))
