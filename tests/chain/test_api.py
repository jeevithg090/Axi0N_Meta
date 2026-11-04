"""The `/chain` routes: list packs, report a pack folder, prove and fit pack YAML."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from rulecam.chain.api import chain_router
from tests.chain.helpers import DEMO


def _client(root: Path, *, owner: bool = True) -> TestClient:
    def require_owner() -> None:
        if not owner:
            raise HTTPException(401, "owner only")

    app = FastAPI()
    app.include_router(chain_router(root, dependencies=[Depends(require_owner)]))
    return TestClient(app)


def _pack_text() -> str:
    return (DEMO / "pack.yaml").read_text(encoding="utf-8")


def _stores_text() -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted((DEMO / "stores").glob("*.yaml"))]


def test_lists_valid_pack_folders_only(tmp_path: Path) -> None:
    shutil.copytree(DEMO, tmp_path / "kitchen-basics")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "pack.yaml").write_text("pack_id: [", encoding="utf-8")
    (tmp_path / "not-a-pack").mkdir()
    [entry] = _client(tmp_path).get("/chain/packs").json()
    assert entry["folder"] == "kitchen-basics"
    assert entry["pack_id"] == "kitchen-basics"
    assert entry["stores"] == 4
    assert entry["simulated"] is True


def test_missing_root_lists_nothing(tmp_path: Path) -> None:
    assert _client(tmp_path / "none").get("/chain/packs").json() == []


def test_report_proves_fits_and_labels_simulated_numbers() -> None:
    response = _client(DEMO.parent).get("/chain/packs/kitchen-basics")
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["releasable"] is True
    assert answer["simulated"] is True
    assert answer["title"] == "Kitchen basics"
    report = answer["report"]
    assert [f["store_id"] for f in report["fits"]] == [
        "store-01",
        "store-07",
        "store-14",
        "store-22",
    ]
    assert report["scorecard"]["provenance"] == "simulated"


def test_unknown_or_bad_folder_names_are_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/chain/packs/nope").status_code == 404
    assert client.get("/chain/packs/..%2Fsecrets").status_code == 404


def test_a_broken_folder_is_422(tmp_path: Path) -> None:
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "pack.yaml").write_text("- not a mapping", encoding="utf-8")
    response = _client(tmp_path).get("/chain/packs/broken")
    assert response.status_code == 422
    assert "mapping" in response.json()["detail"]


def test_prove_and_fit_take_yaml(tmp_path: Path) -> None:
    client = _client(tmp_path)
    proof = client.post("/chain/prove", json={"pack_yaml": _pack_text()})
    assert proof.status_code == 200, proof.text
    assert proof.json()["releasable"] is True
    assert proof.json()["proof"]["pack_id"] == "kitchen-basics"
    body = {"pack_yaml": _pack_text(), "stores_yaml": _stores_text()}
    fits = client.post("/chain/fit", json=body)
    assert fits.status_code == 200, fits.text
    assert len(fits.json()) == 4


def test_bad_yaml_and_repeated_stores_are_422(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/chain/prove", json={"pack_yaml": "a: ["}).status_code == 422
    store = _stores_text()[0]
    repeated = client.post(
        "/chain/fit", json={"pack_yaml": _pack_text(), "stores_yaml": [store, store]}
    )
    assert repeated.status_code == 422
    assert "unique" in repeated.json()["detail"]


def test_routes_are_owner_only(tmp_path: Path) -> None:
    client = _client(tmp_path, owner=False)
    assert client.get("/chain/packs").status_code == 401
    assert client.post("/chain/prove", json={"pack_yaml": "x"}).status_code == 401
