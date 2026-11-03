"""Tests for supermarket shelf gaps: Spark proposes, SAM 3.1 confirms; routes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.ask.vision import Frame
from rulecam.clients.errors import ModelResponseError
from rulecam.contracts import Detection, Mask
from rulecam.shelf import ShelfError, ShelfScanner, clamp, cover, shelf_router
from rulecam.shelf.gaps import to_pixels
from tests.fakes import FakeLLM

AT = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
W, H = 400, 200


def shelf_image(tmp_path: Path) -> Path:
    path = tmp_path / "shelf.jpg"
    cv2.imwrite(str(path), np.full((H, W, 3), 200, dtype=np.uint8))
    return path


def product(box: tuple[float, float, float, float]) -> Detection:
    return Detection(
        camera_id="aisle",
        frame_idx=0,
        ts=AT,
        concept="product package",
        object_id=1,
        mask=Mask(rle="", box_xyxy=box),
    )


class FakeSam:
    def __init__(self, products: list[tuple[float, float, float, float]]) -> None:
        self.products = products
        self.calls: list[str] = []

    def segment_image(
        self, frame: Path, concept: str, *, camera_id: str = "", include_confidence: bool = True
    ) -> list[Detection]:
        self.calls.append(concept)
        return [product(b) for b in self.products]


class Files:
    def attach(self, path: Path) -> dict[str, object]:
        return {"type": "input_image", "file_id": path.name}


def gap(box: list[float], section: str = "bay 1, shelf 2") -> dict[str, Any]:
    return {"box": box, "section": section, "neighbours": "CORN FLAKES above", "facings": 3}


def scanner(tmp_path: Path, spark: FakeLLM, sam: FakeSam, frames: Any = None) -> ShelfScanner:
    return ShelfScanner(
        spark=spark, files=Files(), sam=sam, out_dir=tmp_path / "marked", frames=frames
    )


def test_geometry() -> None:
    assert to_pixels([0, 500, 1000, 1000], W, H) == (0.0, 100.0, 400.0, 200.0)
    assert clamp([450, -5, 10, 90], W, H) == (10.0, 0.0, 400.0, 90.0)
    assert cover((0, 0, 100, 100), []) == 0.0
    cell = 4 / 100  # one 4 px grid cell of a 100 px box
    assert cover((0, 0, 100, 100), [(0, 0, 50, 100)]) == pytest.approx(0.5, abs=cell)
    both = [(0, 0, 50, 100), (25, 0, 75, 100)]  # overlap counted once
    assert cover((0, 0, 100, 100), both) == pytest.approx(0.75, abs=cell)
    assert cover((0, 0, 100, 100), [(200, 200, 300, 300)]) == 0.0


def test_a_gap_counts_only_when_sam_finds_no_product_in_it(tmp_path: Path) -> None:
    spark = FakeLLM(
        [
            {
                "gaps": [
                    gap([0, 0, 500, 500], "bay 1, shelf 1"),
                    gap([500, 0, 1000, 500], "bay 2, shelf 1"),
                    gap([0, 600, 10, 610], "speck"),
                ]
            }
        ]
    )
    sam = FakeSam([(200, 0, 400, 100)])  # fills bay 2, shelf 1
    report = scanner(tmp_path, spark, sam).scan_image(
        shelf_image(tmp_path), camera_id="aisle", at=AT
    )
    assert (report.width, report.height, report.products_seen) == (W, H, 1)
    assert [(g.number, g.section, g.product_cover) for g in report.gaps] == [
        (1, "bay 1, shelf 1", 0.0)
    ]
    assert [r.reason for r in report.rejected] == [
        "SAM 3.1 finds products covering 100% of it",
        "too small",
    ]
    assert report.marked_image is not None
    assert Path(report.marked_image).is_file()
    assert sam.calls == ["product package"]
    messages: Any = spark.calls[0]["messages"]
    content = messages[1]["content"]
    assert content[1] == {"type": "input_image", "file_id": "shelf.jpg"}


def test_no_gaps_no_marked_image(tmp_path: Path) -> None:
    report = scanner(tmp_path, FakeLLM([{"gaps": []}]), FakeSam([])).scan_image(
        shelf_image(tmp_path)
    )
    assert report.gaps == []
    assert report.marked_image is None
    assert report.camera_id == "upload"


def test_unreadable_image(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(ShelfError):
        scanner(tmp_path, FakeLLM([]), FakeSam([])).scan_image(bad)


class Grabber:
    def __init__(self, path: Path | None) -> None:
        self.path = path

    def frames(self, camera_id: str, instants: list[datetime]) -> list[Frame]:
        if self.path is None:
            return []
        return [Frame(camera_id=camera_id, at=instants[0], path=self.path)]


def test_scan_camera(tmp_path: Path) -> None:
    image = shelf_image(tmp_path)
    found = scanner(
        tmp_path, FakeLLM([{"gaps": [gap([0, 0, 500, 500])]}]), FakeSam([]), Grabber(image)
    ).scan_camera("aisle", AT)
    assert (found.camera_id, found.at) == ("aisle", AT)
    with pytest.raises(ShelfError):
        scanner(tmp_path, FakeLLM([]), FakeSam([]), Grabber(None)).scan_camera("aisle", AT)
    with pytest.raises(ShelfError):
        scanner(tmp_path, FakeLLM([]), FakeSam([])).scan_camera("aisle", AT)


def app_for(tmp_path: Path, svc: ShelfScanner | None) -> TestClient:
    app = FastAPI()
    app.include_router(
        shelf_router(svc, uploads_dir=tmp_path / "uploads", marked_dir=tmp_path / "marked")
    )
    return TestClient(app)


def test_routes(tmp_path: Path) -> None:
    image = shelf_image(tmp_path)
    spark = FakeLLM([{"gaps": [gap([0, 0, 500, 500])]}, {"gaps": []}])
    svc = scanner(tmp_path, spark, FakeSam([]), Grabber(image))
    with app_for(tmp_path, svc) as client:
        reply = client.post(
            "/shelf/scan-image", files={"image": ("s.jpg", image.read_bytes(), "image/jpeg")}
        )
        assert reply.status_code == 200
        marked = Path(reply.json()["marked_image"]).name
        assert client.get(f"/shelf/marked/{marked}").status_code == 200
        assert client.get("/shelf/marked/..%2Fsecret.jpg").status_code == 404
        assert client.get("/shelf/marked/nope.png").status_code == 404
        gif = client.post("/shelf/scan-image", files={"image": ("s.gif", b"GIF", "image/gif")})
        assert gif.status_code == 422
        scan = client.post("/shelf/scan", json={"camera_id": "aisle", "at": AT.isoformat()})
        assert scan.status_code == 200
    with app_for(tmp_path, scanner(tmp_path, FakeLLM([]), FakeSam([]), Grabber(None))) as client:
        missing = client.post("/shelf/scan", json={"camera_id": "aisle", "at": AT.isoformat()})
        assert missing.status_code == 422
    with app_for(tmp_path, None) as client:
        assert (
            client.post("/shelf/scan", json={"camera_id": "a", "at": AT.isoformat()}).status_code
            == 503
        )


def test_routes_report_model_failures(tmp_path: Path) -> None:
    class Failing(FakeLLM):
        def structured(self, **kwargs: Any) -> Any:
            raise ModelResponseError("down")

    image = shelf_image(tmp_path)
    with app_for(tmp_path, scanner(tmp_path, Failing(), FakeSam([]), Grabber(image))) as client:
        assert (
            client.post("/shelf/scan", json={"camera_id": "a", "at": AT.isoformat()}).status_code
            == 502
        )
        upload = client.post(
            "/shelf/scan-image", files={"image": ("s.jpg", image.read_bytes(), "image/jpeg")}
        )
        assert upload.status_code == 502
