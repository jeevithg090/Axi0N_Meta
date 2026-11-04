"""`/chain` (owner-only): Chain Mode over HTTP, the same steps as `rulecam chain`.

- `GET /chain/packs`: every pack folder under `RULECAM_CHAINS_DIR` (`pack.yaml`, `stores/`,
  optional `simulation.yaml`), with its id, version, title and store count.
- `GET /chain/packs/{folder}`: the full report for one folder: the pack's proof, every store's
  fit and, when the folder has a `simulation.yaml`, a scorecard whose `provenance` is
  `simulated` and `"simulated": true` (the numbers are SIMULATED, never mixed with real ones).
- `POST /chain/prove` with `{"pack_yaml": "..."}`: lint the pack and run Rule CI at every knob
  value stores may pick.
- `POST /chain/fit` with `{"pack_yaml": "...", "stores_yaml": ["...", ...]}`: fit the pack to
  each store setup.

A pack or store file that does not validate answers 422 with the reason. The routes run Rule CI
(a few seconds per pack), so they are plain functions FastAPI runs off the event loop.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from rulecam.chain.errors import ChainError
from rulecam.chain.fit import fit_chain, load_stores, parse_store
from rulecam.chain.models import ChainReport, PackProof, StoreFit
from rulecam.chain.pack import load_pack, parse_pack, prove_pack
from rulecam.chain.report import build_report
from rulecam.chain.simulate import load_simulation

FOLDER_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_YAML_CHARS: Final = 200_000
MAX_STORES: Final = 200
PACK_FILE: Final = "pack.yaml"
STORES_DIR: Final = "stores"
SIMULATION_FILE: Final = "simulation.yaml"


class PackSummary(BaseModel):
    """One pack folder, as listed."""

    model_config = ConfigDict(frozen=True)

    folder: str
    pack_id: str
    version: int
    title: str
    publisher: str
    vertical: str
    templates: int
    stores: int
    simulated: bool


class ProofAnswer(BaseModel):
    """A pack's proof, with whether it may be released."""

    model_config = ConfigDict(frozen=True)

    releasable: bool
    proof: PackProof


class ReportAnswer(BaseModel):
    """One pack folder's report.

    Attributes:
        folder: The folder.
        title: The pack's title.
        releasable: Whether the pack's proof passed.
        simulated: Whether the scorecard's numbers are SIMULATED (from `simulation.yaml`).
        report: The proof, the fits and the scorecard.
    """

    model_config = ConfigDict(frozen=True)

    folder: str
    title: str
    releasable: bool
    simulated: bool
    report: ChainReport


class ProveRequest(BaseModel):
    """A pack to prove."""

    model_config = ConfigDict(extra="forbid")

    pack_yaml: str = Field(min_length=1, max_length=MAX_YAML_CHARS)


class FitRequest(BaseModel):
    """A pack and the store setups to fit it to."""

    model_config = ConfigDict(extra="forbid")

    pack_yaml: str = Field(min_length=1, max_length=MAX_YAML_CHARS)
    stores_yaml: list[str] = Field(min_length=1, max_length=MAX_STORES)


def pack_folders(root: Path) -> list[Path]:
    """Every folder under `root` holding a `pack.yaml`, by name."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / PACK_FILE).is_file())


def summary(folder: Path) -> PackSummary:
    """A pack folder's listing entry.

    Raises:
        ChainError: If the pack or a store file does not validate.
    """
    pack = load_pack(folder / PACK_FILE)
    stores = folder / STORES_DIR
    return PackSummary(
        folder=folder.name,
        pack_id=pack.pack_id,
        version=pack.version,
        title=pack.title,
        publisher=pack.publisher,
        vertical=pack.vertical,
        templates=len(pack.templates),
        stores=len(load_stores(stores)) if stores.is_dir() else 0,
        simulated=(folder / SIMULATION_FILE).is_file(),
    )


def folder_report(folder: Path) -> ReportAnswer:
    """Prove, fit and (with a simulation) score one pack folder, as `rulecam chain demo` does.

    Raises:
        ChainError: If a file does not validate.
    """
    pack = load_pack(folder / PACK_FILE)
    stores = folder / STORES_DIR
    sim = folder / SIMULATION_FILE
    report = build_report(
        pack,
        load_stores(stores) if stores.is_dir() else [],
        simulation=load_simulation(sim) if sim.is_file() else None,
    )
    return ReportAnswer(
        folder=folder.name,
        title=pack.title,
        releasable=report.proof.releasable,
        simulated=sim.is_file(),
        report=report,
    )


def list_packs(root: Path) -> list[PackSummary]:
    """Every pack folder that validates, by folder name (broken ones are left out)."""
    found: list[PackSummary] = []
    for folder in pack_folders(root):
        try:
            found.append(summary(folder))
        except ChainError:
            continue
    return found


def _unprocessable(exc: ChainError) -> HTTPException:
    return HTTPException(422, str(exc))


def _report(root: Path, folder: str) -> ReportAnswer:
    path = root / folder
    if not FOLDER_PATTERN.match(folder) or not (path / PACK_FILE).is_file():
        raise HTTPException(404, f"no pack folder {folder!r}")
    try:
        return folder_report(path)
    except ChainError as exc:
        raise _unprocessable(exc) from exc


def _prove(body: ProveRequest) -> ProofAnswer:
    try:
        proof = prove_pack(parse_pack(body.pack_yaml, source="request"))
    except ChainError as exc:
        raise _unprocessable(exc) from exc
    return ProofAnswer(releasable=proof.releasable, proof=proof)


def _fit(body: FitRequest) -> list[StoreFit]:
    try:
        pack = parse_pack(body.pack_yaml, source="request")
        stores = [
            parse_store(text, source=f"store {i + 1}") for i, text in enumerate(body.stores_yaml)
        ]
        return list(fit_chain(pack, stores))
    except ChainError as exc:
        raise _unprocessable(exc) from exc


def chain_router(root: Path, *, dependencies: Sequence[Any] = ()) -> APIRouter:
    """The `/chain` routes.

    Args:
        root: The folder holding one sub-folder per pack (`RULECAM_CHAINS_DIR`).
        dependencies: Route dependencies (the owner check).

    Returns:
        The router.
    """
    router = APIRouter(prefix="/chain", tags=["chain"], dependencies=list(dependencies))

    @router.get("/packs", response_model=list[PackSummary])
    def packs() -> list[PackSummary]:
        return list_packs(root)

    @router.get("/packs/{folder}", response_model=ReportAnswer)
    def report(folder: str) -> ReportAnswer:
        return _report(root, folder)

    @router.post("/prove", response_model=ProofAnswer)
    def prove(body: ProveRequest) -> ProofAnswer:
        return _prove(body)

    @router.post("/fit", response_model=list[StoreFit])
    def fit(body: FitRequest) -> list[StoreFit]:
        return _fit(body)

    return router
