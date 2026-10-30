"""Fixtures for Pair-a-Phone tests: a bare FastAPI app, phone/owner clients and tiny clips."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.bus import Bus, Topic
from rulecam.capture.pair_phone import SESSION_HEADER, PairPhone, build_router
from rulecam.config import Settings
from rulecam.contracts import Detection, Mask
from tests.fakes import EPOCH, FakeClock

TUNNEL = "https://rulecam-demo.trycloudflare.com"
PHONE_ADDR = ("203.0.113.7", 50123)
OWNER_ADDR = ("127.0.0.1", 50124)


class RecordingBus(Bus):
    """A bus that also remembers everything published, for assertions across threads."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[Topic, object]] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.published.append((topic, payload))
        super().publish(topic, payload)


class FakeFrameSegmenter:
    """Single-frame segmenter returning a preset confidence per concept; records calls."""

    def __init__(self, confidences: dict[str, float | None] | None = None) -> None:
        self.confidences = confidences or {}
        self.calls: list[tuple[Path, str]] = []

    def segment_image(self, frame: Path, concept: str) -> list[Detection]:
        self.calls.append((frame, concept))
        assert frame.exists(), "the coach must hand the segmenter a real frame"
        if concept not in self.confidences:
            return []
        mask = Mask(rle="fake", box_xyxy=(0, 0, 10, 10), confidence=self.confidences[concept])
        return [
            Detection(
                camera_id="phone", frame_idx=0, ts=EPOCH, concept=concept, object_id=1, mask=mask
            )
        ]


def make_clip(path: Path, *, seconds: float, codec: str = "libvpx", live: bool = False) -> Path:
    """Render a tiny `testsrc` clip with ffmpeg (no network, deterministic)."""
    args = [
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=30",
        "-t",
        f"{seconds}",
        "-c:v",
        codec,
        "-pix_fmt",
        "yuv420p",
        *(["-live", "1"] if live else []),
        "-y",
        str(path),
    ]
    subprocess.run(args, check=True)  # noqa: S603
    return path


@dataclass
class Phone:
    """A paired phone: its client, token, camera id and session key."""

    client: TestClient
    token: str
    camera_id: str
    session: str

    @property
    def headers(self) -> dict[str, str]:
        return {SESSION_HEADER: self.session}

    def upload(
        self, data: bytes, content_type: str = "video/webm", name: str = "clip.webm"
    ) -> httpx.Response:
        response: httpx.Response = self.client.post(
            f"/pair/{self.token}/chunk",
            headers=self.headers,
            files={"file": (name, data, content_type)},
        )
        return response


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()


@pytest.fixture
def segmenter() -> FakeFrameSegmenter:
    return FakeFrameSegmenter({"sink": 0.91, "soap dispenser": 0.42})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, public_tunnel_url=TUNNEL, rulecam_data_dir=tmp_path / "data")


@pytest.fixture
def service(
    bus: RecordingBus, clock: FakeClock, segmenter: FakeFrameSegmenter, settings: Settings
) -> PairPhone:
    return PairPhone(
        bus=bus,
        clock=clock,
        segmenter=segmenter,
        settings=settings,
        concepts=lambda: ("sink", "soap dispenser", "paper towels"),
        coach_interval_s=0.05,
    )


@pytest.fixture
def make_client(service: PairPhone) -> Iterator[Callable[..., TestClient]]:
    app = FastAPI()
    app.include_router(build_router(service))
    clients: list[TestClient] = []

    def factory(
        base_url: str = "https://testserver", client: tuple[str, int] = PHONE_ADDR
    ) -> TestClient:
        test_client = TestClient(app, base_url=base_url, client=client)
        clients.append(test_client)
        return test_client

    yield factory
    for test_client in clients:
        test_client.close()


@pytest.fixture
def phone_client(make_client: Callable[..., TestClient]) -> TestClient:
    """A phone reaching the API through the HTTPS tunnel."""
    return make_client()


@pytest.fixture
def owner_client(make_client: Callable[..., TestClient]) -> TestClient:
    """The owner UI on the same machine as the API."""
    return make_client(base_url="http://localhost:8000", client=OWNER_ADDR)


@pytest.fixture
def phone(service: PairPhone, phone_client: TestClient, clock: FakeClock) -> Phone:
    """A phone that has claimed a fresh token (the rate-limit window has already passed)."""
    token = service.registry.issue().token
    response = phone_client.post(f"/pair/{token}/claim", json={"label": "Sink wall"})
    assert response.status_code == 201, response.text
    body = response.json()
    clock.advance(1.0)
    return Phone(phone_client, token, body["camera_id"], body["session"])


@pytest.fixture
def webm_clip(tmp_path: Path) -> bytes:
    return make_clip(tmp_path / "clip.webm", seconds=3).read_bytes()


@pytest.fixture
def mp4_clip(tmp_path: Path) -> bytes:
    return make_clip(tmp_path / "clip.mp4", seconds=3, codec="libx264").read_bytes()
