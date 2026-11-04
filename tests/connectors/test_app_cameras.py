"""`GET /cameras` shows connector cameras by the name Spark gave them, with its description."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from rulecam.api.app import AppServices, CameraInfo, create_app
from rulecam.bus import Bus
from rulecam.clock import SystemClock
from rulecam.connectors.manager import ConnectorManager, ConnectorSettings
from rulecam.connectors.sources import SourceKind
from rulecam.connectors.store import CameraRecord
from rulecam.connectors.survey import CameraSurvey
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.factory import ChunkIndex
from tests.connectors.test_survey import LOOK
from tests.fakes import EPOCH, FakeClock, make_chunk


def manager_with_cameras(root: Path) -> ConnectorManager:
    manager = ConnectorManager(ConnectorSettings(root=root), bus=Bus(), clock=SystemClock())
    store = manager._store
    for camera_id, name in (("cam-1", "Checkout lanes 1-3"), ("cam-2", "Dock door")):
        store.put(
            CameraRecord(
                camera_id=camera_id,
                name=name,
                named_by="spark",
                kind=SourceKind.RTSP,
                url=f"rtsp://10.0.0.5/{camera_id}",
                created_at=EPOCH,
            )
        )
    for camera_id in ("cam-1", "phone-3"):
        manager._set_survey(CameraSurvey(camera_id=camera_id, status="done", look=LOOK))
    return manager


def test_label_rows(tmp_path: Path) -> None:
    manager = manager_with_cameras(tmp_path)
    rows = [
        CameraInfo(camera_id="cam-1", label="cam-1", source="capture", last_chunk_at=EPOCH),
        CameraInfo(camera_id="phone-3", label="Till phone", source="paired_phone"),
        CameraInfo(camera_id="cap-9", label="cap-9", source="capture"),
    ]
    out = {row.camera_id: row for row in manager.label(rows, CameraInfo)}
    assert out["cam-1"].label == "Checkout lanes 1-3"
    assert out["cam-1"].source == "connector-rtsp"
    assert out["cam-1"].description == LOOK.description
    assert out["cam-1"].last_chunk_at == EPOCH
    assert out["phone-3"].label == "Till phone"  # the owner's phone name stays
    assert out["phone-3"].description == LOOK.description
    assert out["cap-9"] == rows[2]
    assert out["cam-2"].label == "Dock door"  # connected, no chunk yet
    assert out["cam-2"].last_chunk_at is None


def test_get_cameras_uses_the_connectors(tmp_path: Path) -> None:
    bus = Bus()
    chunks = ChunkIndex()
    chunks.record(make_chunk("cam-1", 0))
    services = AppServices(
        bus=bus,
        cache=ReplayCache(tmp_path / "replay", "replay", clock=FakeClock(), bus=bus),
        chain_root=tmp_path / "chain",
        site="site",
        chunks=chunks,
        connectors=manager_with_cameras(tmp_path / "connectors"),
    )
    with TestClient(create_app(services), base_url="http://localhost") as client:
        cameras = {c["camera_id"]: c for c in client.get("/cameras").json()}
    assert cameras["cam-1"]["label"] == "Checkout lanes 1-3"
    assert cameras["cam-1"]["description"] == LOOK.description
    assert cameras["cam-1"]["last_chunk_at"] is not None
    assert cameras["cam-2"]["source"] == "connector-rtsp"
