"""Proof portal: verifiable proof packs for inspectors, insurers, HQ and worker representatives.

A proof pack turns a site's hash-chained evidence log into the numbers a regulator, an insurer,
a chain's head office or the crew's representative asks for, and discloses the chain records each
number was computed from with Merkle inclusion proofs to the daily roots, so the recipient's
browser can recompute every hash, every root and every number without trusting the site.
"""

from rulecam.compliance.build import BuildInputs, build_pack, period_for
from rulecam.compliance.engine import MetricSpec, Period, Shift, evaluate
from rulecam.compliance.models import PackRequest, ProofPack

__all__ = [
    "BuildInputs",
    "MetricSpec",
    "PackRequest",
    "Period",
    "ProofPack",
    "Shift",
    "build_pack",
    "evaluate",
    "period_for",
]
