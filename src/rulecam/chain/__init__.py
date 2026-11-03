"""Chain Mode: one rule pack, many stores (see `README.md` in this package).

Head office writes a rule pack once (`pack`), proves it across every value stores may pick,
fits it to each store's own cameras (`fit`), scores the stores at store level only
(`scorecard`), and commits every store's evidence under one daily root (`rollup`).
"""

from rulecam.chain.errors import (
    ChainError,
    ObservationsError,
    PackError,
    RollupError,
    StoreSetupError,
)
from rulecam.chain.fit import fit_chain, fit_rule, fit_store, load_store, load_stores
from rulecam.chain.models import (
    ChainObservations,
    ChainReport,
    ChainRoot,
    ChainScorecard,
    FittedRule,
    FixItem,
    InclusionProof,
    Knob,
    PackFinding,
    PackProof,
    RulePack,
    RuleScore,
    RuleTemplate,
    Slot,
    StoreFit,
    StoreObservations,
    StoreRoot,
    StoreSetup,
)
from rulecam.chain.pack import lint_pack, load_pack, parse_pack, prove_pack
from rulecam.chain.report import build_report, render_fits, render_proof, render_report
from rulecam.chain.rollup import chain_root, inclusion_proof, load_roots, verify_inclusion
from rulecam.chain.scorecard import chain_scorecard
from rulecam.chain.simulate import Simulation, load_simulation, simulate

__all__ = [
    "ChainError",
    "ChainObservations",
    "ChainReport",
    "ChainRoot",
    "ChainScorecard",
    "FittedRule",
    "FixItem",
    "InclusionProof",
    "Knob",
    "ObservationsError",
    "PackError",
    "PackFinding",
    "PackProof",
    "RollupError",
    "RulePack",
    "RuleScore",
    "RuleTemplate",
    "Simulation",
    "Slot",
    "StoreFit",
    "StoreObservations",
    "StoreRoot",
    "StoreSetup",
    "StoreSetupError",
    "build_report",
    "chain_root",
    "chain_scorecard",
    "fit_chain",
    "fit_rule",
    "fit_store",
    "inclusion_proof",
    "lint_pack",
    "load_pack",
    "load_roots",
    "load_simulation",
    "load_store",
    "load_stores",
    "parse_pack",
    "prove_pack",
    "render_fits",
    "render_proof",
    "render_report",
    "simulate",
    "verify_inclusion",
]
