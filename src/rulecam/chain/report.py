"""The HQ report: prove the pack, fit every store, score them, and print it for people."""

from __future__ import annotations

from collections.abc import Sequence

from rulecam.chain.errors import ObservationsError
from rulecam.chain.fit import fit_chain
from rulecam.chain.models import (
    ChainObservations,
    ChainReport,
    ChainScorecard,
    PackProof,
    RulePack,
    RuleScore,
    StoreFit,
    StoreSetup,
)
from rulecam.chain.pack import prove_pack
from rulecam.chain.scorecard import chain_scorecard
from rulecam.chain.simulate import Simulation, simulate
from rulecam.contracts.core import Provenance

_CELL_WIDTH = 10


def build_report(
    pack: RulePack,
    stores: Sequence[StoreSetup],
    *,
    observations: ChainObservations | None = None,
    simulation: Simulation | None = None,
) -> ChainReport:
    """Prove the pack, fit it to every store and, given observations, score them.

    Args:
        pack: The pack.
        stores: The stores.
        observations: What the stores reported, if anything.
        simulation: Instead of observations: simulate them for the fitted stores (SIMULATED).

    Returns:
        The report.

    Raises:
        ObservationsError: Both observations and a simulation were given.
    """
    if observations is not None and simulation is not None:
        msg = "give observations or a simulation, not both"
        raise ObservationsError(msg)
    fits = fit_chain(pack, stores)
    if simulation is not None:
        observations = simulate(fits, simulation)
    scorecard = chain_scorecard(fits, observations) if observations is not None else None
    return ChainReport(proof=prove_pack(pack), fits=fits, scorecard=scorecard)


def render_proof(proof: PackProof) -> str:
    """A pack's proof as plain text."""
    return "\n".join(_proof_lines(proof)) + "\n"


def render_fits(fits: Sequence[StoreFit]) -> str:
    """Every store's fit as plain text."""
    return "\n".join(_fit_lines(fits)) + "\n"


def _proof_lines(proof: PackProof) -> list[str]:
    verdict = "RELEASABLE" if proof.releasable else "NOT RELEASABLE"
    settings = sum(len(t.settings) for t in proof.templates)
    lines = [
        f"1. Pack proof: {verdict} ({len(proof.templates)} rules, Rule CI at {settings} settings)"
    ]
    for template in proof.templates:
        state = "green" if template.passed else "RED"
        labels = ", ".join(s.label for s in template.settings)
        lines.append(f"   {template.rule_id:<28} {state:<5} at {labels}")
    lines += [f"   ! [{f.code}] {f.rule_id}: {f.message}" for f in proof.findings]
    return lines


def _fit_lines(fits: Sequence[StoreFit]) -> list[str]:
    lines = ["2. Stores"]
    for fit in fits:
        where = f" ({fit.region})" if fit.region else ""
        lines.append(
            f"   {fit.store_id} {fit.store_name}{where}: {fit.ready_count}/{len(fit.rules)} ready"
        )
        for rule in fit.rules:
            lines.append(f"     {rule.rule_id:<28} {rule.status:<11} {rule.says}")
            lines += [f"       ! {w}" for w in rule.warnings]
        lines += [f"     ! {w}" for w in fit.warnings]
    return lines


def _cell(score: RuleScore) -> str:
    if score.status == "not_ready":
        return "off"
    if score.status == "camera_blind":
        return "BLIND"
    if score.rate is None:
        return "few"
    flag = "*" if score.status == "below_chain" else ""
    return f"{score.rate:.0%}{flag}"


def _score_lines(card: ChainScorecard) -> list[str]:
    stores = list(dict.fromkeys(s.store_id for s in card.scores))
    label = " SIMULATED" if card.provenance is Provenance.SIMULATED else ""
    lines = [
        f"3. Scorecard{label} ({card.start:%Y-%m-%d %H:%M} to {card.end:%Y-%m-%d %H:%M} UTC, "
        f"{card.provenance})",
        "   "
        + f"{'rule':<28} {'chain':>{_CELL_WIDTH}}"
        + "".join(f"{s:>{_CELL_WIDTH}}" for s in stores),
    ]
    by_key = {(s.store_id, s.rule_id): s for s in card.scores}
    for rule_id, chain in card.chain_rates.items():
        chain_text = "n/a" if chain is None else f"{chain:.0%}"
        cells = "".join(f"{_cell(by_key[s, rule_id]):>{_CELL_WIDTH}}" for s in stores)
        lines.append(f"   {rule_id:<28} {chain_text:>{_CELL_WIDTH}}{cells}")
    lines.append(
        "   * below the chain at 95%; BLIND: camera not watching; few: under the small-crew "
        "floor; off: not running"
    )
    return lines


def _fix_lines(card: ChainScorecard) -> list[str]:
    lines = ["4. Fix first"]
    lines += [
        f"   {i}. [{item.severity.upper()}] {item.says}"
        for i, item in enumerate(card.fix_first, start=1)
    ]
    return lines if card.fix_first else [*lines, "   nothing to fix"]


def render_report(report: ChainReport, *, title: str = "") -> str:
    """The report as plain text for the terminal.

    Args:
        report: The report.
        title: The pack's title, for the heading.

    Returns:
        The text, ending with a newline.
    """
    proof = report.proof
    head = f"Chain Mode: {title or proof.pack_id} v{proof.version}"
    lines = [f"{head} ({proof.pack_id}, sha256 {proof.digest[:12]})", ""]
    lines += [*_proof_lines(proof), "", *_fit_lines(report.fits)]
    if report.scorecard is not None:
        lines += ["", *_score_lines(report.scorecard), "", *_fix_lines(report.scorecard)]
    return "\n".join(lines) + "\n"
