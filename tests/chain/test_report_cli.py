"""The demo report, the simulator and the `rulecam chain` commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rulecam.chain import (
    ChainReport,
    ObservationsError,
    build_report,
    fit_chain,
    load_pack,
    load_simulation,
    load_stores,
    render_report,
    simulate,
)
from rulecam.chain.simulate import Simulation
from rulecam.cli import app
from rulecam.contracts.core import Provenance
from tests.chain.helpers import DEMO

runner = CliRunner()


@pytest.fixture(scope="module")
def report() -> ChainReport:
    return build_report(
        load_pack(DEMO / "pack.yaml"),
        load_stores(DEMO / "stores"),
        simulation=load_simulation(DEMO / "simulation.yaml"),
    )


def test_the_demo_report_covers_proof_fits_and_scorecard(report: ChainReport) -> None:
    assert report.proof.releasable
    assert [f.store_id for f in report.fits] == ["store-01", "store-07", "store-14", "store-22"]
    card = report.scorecard
    assert card is not None
    assert card.provenance is Provenance.SIMULATED
    statuses = {(s.store_id, s.rule_id): s.status for s in card.scores}
    assert statuses["store-07", "handwash-before-line"] == "below_chain"
    assert statuses["store-14", "handwash-before-line"] == "camera_blind"
    assert statuses["store-22", "delivery-to-cold-storage"] == "not_ready"
    assert card.fix_first[0].severity == "high"


def test_the_text_report_labels_simulated_numbers(report: ChainReport) -> None:
    text = render_report(report, title="Kitchen basics")
    assert text.startswith("Chain Mode: Kitchen basics v3")
    assert "1. Pack proof: RELEASABLE" in text
    assert "3. Scorecard SIMULATED" in text
    assert "62%*" in text
    assert "4. Fix first" in text


def test_the_simulation_is_seeded() -> None:
    pack, stores = load_pack(DEMO / "pack.yaml"), load_stores(DEMO / "stores")
    fits = fit_chain(pack, stores)
    sim = load_simulation(DEMO / "simulation.yaml")
    assert simulate(fits, sim) == simulate(fits, sim)
    other = simulate(fits, sim.model_copy(update={"seed": sim.seed + 1}))
    assert other != simulate(fits, sim)
    blind = simulate(fits, sim).stores["store-14"].coverage["kitchen-cam"]
    assert any(not s.watched and s.reason == "moved" for s in blind)


def test_observations_and_a_simulation_cannot_both_be_given(report: ChainReport) -> None:
    pack, stores = load_pack(DEMO / "pack.yaml"), load_stores(DEMO / "stores")
    sim = Simulation.model_validate({"seed": 1, "start": "2026-11-02T08:00:00Z", "hours": 1})
    observations = simulate(report.fits, sim)
    with pytest.raises(ObservationsError, match="not both"):
        build_report(pack, stores, observations=observations, simulation=sim)


def test_load_simulation_reports_bad_files(tmp_path: Path) -> None:
    bad = tmp_path / "sim.yaml"
    bad.write_text("seed: x", encoding="utf-8")
    with pytest.raises(ObservationsError, match="cannot load simulation"):
        load_simulation(bad)


# --- the CLI -------------------------------------------------------------------------------


def test_cli_prove_passes_the_demo_pack_and_fails_a_broken_one(tmp_path: Path) -> None:
    ok = runner.invoke(app, ["chain", "prove", str(DEMO / "pack.yaml")])
    assert ok.exit_code == 0, ok.output
    assert "RELEASABLE" in ok.output
    broken = tmp_path / "pack.yaml"
    text = (DEMO / "pack.yaml").read_text("utf-8").replace("zone: line_entry", "zone: nowhere")
    broken.write_text(text, encoding="utf-8")
    bad = runner.invoke(app, ["chain", "prove", str(broken)])
    assert bad.exit_code == 1
    assert "NOT RELEASABLE" in bad.output
    assert "undefined_slot" in bad.output


def test_cli_fit_writes_every_store(tmp_path: Path) -> None:
    out = tmp_path / "fits.json"
    result = runner.invoke(
        app, ["chain", "fit", str(DEMO / "pack.yaml"), str(DEMO / "stores"), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "store-22 Airport (east): 1/3 ready" in result.output
    assert [f["store_id"] for f in json.loads(out.read_text("utf-8"))][:1] == ["store-01"]


def test_cli_demo_prints_and_writes_the_report(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    result = runner.invoke(app, ["chain", "demo", str(DEMO), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "3. Scorecard SIMULATED" in result.output
    assert ChainReport.model_validate_json(out.read_text("utf-8")).proof.releasable


def test_cli_rollup_and_verify_proof(tmp_path: Path) -> None:
    roots = []
    for i in range(3):
        path = tmp_path / f"roots-{i}.txt"
        path.write_text(f"store-{i} {f'{i}' * 64} {i + 2}\n", encoding="utf-8")
        roots.append(str(path))
    proofs = tmp_path / "proofs"
    result = runner.invoke(app, ["chain", "rollup", "2026-11-02", *roots, "--proofs", str(proofs)])
    assert result.exit_code == 0, result.output
    assert "HQ root" in result.output
    proof = proofs / "2026-11-02-store-1.proof.json"
    checked = runner.invoke(app, ["chain", "verify-proof", str(proof)])
    assert checked.exit_code == 0, checked.output
    assert checked.output.startswith("OK: store-1")
    data = json.loads(proof.read_text("utf-8"))
    data["store"]["n_records"] += 1
    proof.write_text(json.dumps(data), encoding="utf-8")
    failed = runner.invoke(app, ["chain", "verify-proof", str(proof)])
    assert failed.exit_code == 1
    assert failed.output.startswith("FAIL")
