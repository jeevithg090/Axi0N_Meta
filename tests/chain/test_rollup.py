"""The HQ evidence root: every store's root under one, with proofs that check on their own."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rulecam.chain import (
    RollupError,
    chain_root,
    inclusion_proof,
    load_roots,
    verify_inclusion,
)
from rulecam.chain.models import ProofStep, StoreRoot
from rulecam.chain.rollup import store_leaf
from rulecam.evidence.merkle import merkle_root

DAY = date(2026, 11, 2)
_HEX = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
_ROOTS = st.dictionaries(
    st.from_regex(r"store-[0-9]{1,3}", fullmatch=True),
    st.tuples(_HEX, st.integers(min_value=0, max_value=10_000)),
    min_size=1,
    max_size=40,
)


def _hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@settings(max_examples=60, deadline=None)
@given(_ROOTS)
def test_every_store_proof_verifies_and_the_root_matches_merkle(
    roots: dict[str, tuple[str, int]],
) -> None:
    chain = chain_root(DAY, roots)
    assert chain.root == merkle_root([store_leaf(s) for s in chain.stores])
    assert [s.store_id for s in chain.stores] == sorted(roots)
    for store in chain.stores:
        proof = inclusion_proof(chain, store.store_id)
        assert verify_inclusion(proof)


@settings(max_examples=40, deadline=None)
@given(_ROOTS, st.integers(min_value=0))
def test_a_changed_store_root_or_record_count_fails(
    roots: dict[str, tuple[str, int]], pick: int
) -> None:
    chain = chain_root(DAY, roots)
    store = chain.stores[pick % len(chain.stores)]
    proof = inclusion_proof(chain, store.store_id)
    edited = store.model_copy(update={"n_records": store.n_records + 1})
    assert not verify_inclusion(proof.model_copy(update={"store": edited}))
    swapped = store.model_copy(update={"root": _hex(store.root)})
    forged_leaf = proof.model_copy(update={"store": swapped, "leaf": store_leaf(swapped)})
    assert not verify_inclusion(forged_leaf)


def test_a_tampered_sibling_or_root_fails() -> None:
    chain = chain_root(DAY, {f"s{i}": (_hex(str(i)), i) for i in range(5)})
    proof = inclusion_proof(chain, "s3")
    first = proof.steps[0]
    bad_step = ProofStep(sibling=_hex("x"), side=first.side)
    assert not verify_inclusion(proof.model_copy(update={"steps": (bad_step, *proof.steps[1:])}))
    assert not verify_inclusion(proof.model_copy(update={"root": _hex("y")}))


def test_one_store_is_its_own_root() -> None:
    chain = chain_root(DAY, {"only": (_hex("a"), 3)})
    proof = inclusion_proof(chain, "only")
    assert chain.root == proof.leaf
    assert proof.steps == ()
    assert verify_inclusion(proof)


def test_the_leaf_hashes_the_roots_file_line() -> None:
    store = StoreRoot(store_id="store-1", root=_hex("r"), n_records=7)
    assert store_leaf(store) == _hex(f"store-1 {_hex('r')} 7")


def test_unknown_stores_and_empty_days_raise() -> None:
    with pytest.raises(RollupError, match="no store roots"):
        chain_root(DAY, {})
    with pytest.raises(RollupError, match="not in"):
        inclusion_proof(chain_root(DAY, {"a": (_hex("a"), 1)}), "b")


def test_roots_files_merge_and_clash(tmp_path: Path) -> None:
    one, two = tmp_path / "one.txt", tmp_path / "two.txt"
    one.write_text(f"store-1 {_hex('1')} 4\n", encoding="utf-8")
    two.write_text(f"store-2 {_hex('2')} 9\n", encoding="utf-8")
    assert load_roots([one, two]) == {"store-1": (_hex("1"), 4), "store-2": (_hex("2"), 9)}
    with pytest.raises(RollupError, match="more than one"):
        load_roots([one, one])
    with pytest.raises(RollupError, match="cannot read"):
        load_roots([tmp_path / "missing.txt"])
