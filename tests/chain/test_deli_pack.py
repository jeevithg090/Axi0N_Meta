"""The supermarket deli pack is releasable and fits its example store."""

from __future__ import annotations

from pathlib import Path

from rulecam.chain.fit import fit_store, load_store
from rulecam.chain.pack import load_pack, prove_pack

DELI = Path(__file__).resolve().parents[2] / "chains" / "supermarket-deli"


def test_the_deli_pack_is_releasable() -> None:
    proof = prove_pack(load_pack(DELI / "pack.yaml"))
    assert proof.releasable
    assert {t.rule_id for t in proof.templates} == {
        "deli-hand-sink-clear",
        "deli-raw-meat-out",
        "fire-exit-clear",
    }


def test_the_example_store_runs_every_rule() -> None:
    pack = load_pack(DELI / "pack.yaml")
    fit = fit_store(pack, load_store(DELI / "stores" / "store-03.yaml"))
    assert fit.ready_count == 3
    meat = fit.rule("deli-raw-meat-out")
    assert meat is not None
    assert meat.knobs == {"out_seconds": 1200.0}
