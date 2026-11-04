"""Merkle inclusion proofs against a day's root (`evidence.merkle`), and the pack's own hash.

A day's root pairs hex leaves as `sha256(left_hex + right_hex)` and pairs an odd last node with
itself. An inclusion proof is the list of siblings from the leaf up to the root, each with the
side it sits on (`L`: the sibling is on the left). Anyone holding a record can recompute its hash
from its contents, fold the proof and compare with the root: the record is in that day's log, in
that position, unchanged, without seeing any other record.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Literal

from rulecam.evidence.chain import canonical_json
from rulecam.evidence.merkle import merkle_root

Side = Literal["L", "R"]
ProofStep = tuple[str, Side]


def _pair(left: str, right: str) -> str:
    return hashlib.sha256((left + right).encode("ascii")).hexdigest()


def inclusion_proofs(leaves: Sequence[str]) -> list[list[ProofStep]]:
    """Return the inclusion proof of every leaf, in leaf order.

    Args:
        leaves: The day's record hashes in chain order.

    Returns:
        One proof per leaf; folding it from the leaf gives `merkle_root(leaves)`.
    """
    proofs: list[list[ProofStep]] = [[] for _ in leaves]
    positions = list(range(len(leaves)))  # each leaf's index in the current level
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        for leaf, index in enumerate(positions):
            sibling = index ^ 1
            proofs[leaf].append((level[sibling], "L" if sibling < index else "R"))
            positions[leaf] = index // 2
        level = [_pair(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return proofs


def fold(leaf: str, proof: Sequence[ProofStep]) -> str:
    """Recompute the root from a leaf and its proof."""
    node = leaf
    for sibling, side in proof:
        node = _pair(sibling, node) if side == "L" else _pair(node, sibling)
    return node


def verify_inclusion(leaf: str, proof: Sequence[ProofStep], root: str) -> bool:
    """Whether `leaf` folds up to `root`."""
    return fold(leaf, proof) == root


def day_root(leaves: Sequence[str]) -> str:
    """The day's root, exactly as `evidence.merkle.merkle_root` computes it."""
    return merkle_root(leaves)


def pack_hash(body: Mapping[str, object]) -> str:
    """SHA-256 of the pack's canonical JSON (the browser recomputes it the same way)."""
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
