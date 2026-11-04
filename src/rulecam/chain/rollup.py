"""One evidence root for the whole chain: a Merkle root over every store's daily root.

Each store already writes `evidence/roots/<day>.txt` lines `<site> <root> <n>` (P3-07b). HQ
hashes each line into a leaf, `sha256("<store> <root> <n>")`, and builds a Merkle root over the
leaves in store id order with the same pairing as `evidence.merkle.merkle_root` (hex text
concatenated, an odd node paired with itself). Publishing one 64-character root a day then
commits every store's chain at once.

An `InclusionProof` lets anyone (an inspector, an insurer, the store itself) check that one
store's root is inside the HQ root with nothing but the proof: no other store's data is needed
or revealed beyond its hash.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Literal

from rulecam.chain.errors import RollupError
from rulecam.chain.models import ChainRoot, InclusionProof, ProofStep, StoreRoot
from rulecam.evidence.merkle import merkle_root, read_day_roots


def _pair(left: str, right: str) -> str:
    return hashlib.sha256((left + right).encode("ascii")).hexdigest()


def store_leaf(store: StoreRoot) -> str:
    """The leaf of one store: the SHA-256 of its roots-file line."""
    line = f"{store.store_id} {store.root} {store.n_records}"
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def chain_root(day: date, roots: Mapping[str, tuple[str, int]]) -> ChainRoot:
    """The HQ root for one day.

    Args:
        day: The UTC day.
        roots: Store id -> (its Merkle root, its record count), e.g. from `read_day_roots`.

    Returns:
        The HQ root with the store roots in leaf order.

    Raises:
        RollupError: No store has a root.
    """
    if not roots:
        msg = f"no store roots for {day.isoformat()}"
        raise RollupError(msg)
    stores = tuple(
        StoreRoot(store_id=store_id, root=root, n_records=n)
        for store_id, (root, n) in sorted(roots.items())
    )
    return ChainRoot(day=day, root=merkle_root([store_leaf(s) for s in stores]), stores=stores)


def load_roots(paths: list[Path]) -> dict[str, tuple[str, int]]:
    """Merge several stores' roots files into one mapping.

    Raises:
        RollupError: A file cannot be read or two files name the same store.
    """
    merged: dict[str, tuple[str, int]] = {}
    for path in paths:
        try:
            roots = read_day_roots(path)
        except (OSError, ValueError) as exc:
            msg = f"cannot read roots file {path}: {exc}"
            raise RollupError(msg) from exc
        clash = sorted(set(roots) & set(merged))
        if clash:
            msg = f"stores {clash} appear in more than one roots file"
            raise RollupError(msg)
        merged.update(roots)
    return merged


def inclusion_proof(chain: ChainRoot, store_id: str) -> InclusionProof:
    """The proof that one store's root is inside the HQ root.

    Args:
        chain: The HQ root.
        store_id: The store.

    Returns:
        The proof, from the store's leaf up to the root.

    Raises:
        RollupError: The store is not in this root.
    """
    position = next((i for i, s in enumerate(chain.stores) if s.store_id == store_id), None)
    if position is None:
        msg = f"store {store_id!r} is not in the {chain.day.isoformat()} root"
        raise RollupError(msg)
    store = chain.stores[position]
    level = [store_leaf(s) for s in chain.stores]
    steps: list[ProofStep] = []
    index = position
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        side: Literal["left", "right"] = "right" if index % 2 == 0 else "left"
        steps.append(ProofStep(sibling=level[index ^ 1], side=side))
        level = [_pair(level[i], level[i + 1]) for i in range(0, len(level), 2)]
        index //= 2
    return InclusionProof(
        day=chain.day, store=store, leaf=store_leaf(store), steps=tuple(steps), root=chain.root
    )


def verify_inclusion(proof: InclusionProof) -> bool:
    """Whether the proof's store root really sits inside its HQ root.

    Recomputes the leaf from the store root, then hashes up through every step.
    """
    node = store_leaf(proof.store)
    if node != proof.leaf:
        return False
    for step in proof.steps:
        node = _pair(node, step.sibling) if step.side == "right" else _pair(step.sibling, node)
    return node == proof.root
