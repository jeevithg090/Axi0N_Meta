"""Unit tests for Muse Image client (`MuseImageClient`) (task P2-01)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import SecretStr

from rulecam.bus import Bus, Topic
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted.image import ImageResult, MuseImageClient
from rulecam.clients.relay import ModelUsed
from rulecam.config import Settings
from rulecam.replay.cache import ReplayCache
from rulecam.replay.errors import ReplayMiss
from tests.clients.meta_hosted.transport_fakes import ScriptedTransport
from tests.fakes import FakeClock


class RecordingBus(Bus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[Topic, object]] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.published.append((topic, payload))
        super().publish(topic, payload)


class MonotonicClock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def test_image_generate_success(tmp_path: Path) -> None:
    fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake_image_content"
    encoded_png = base64.b64encode(fake_png).decode("ascii")
    reply = {
        "status": 200,
        "headers": {},
        "json": {
            "id": "img_resp_123",
            "data": [{"b64_json": encoded_png}],
        },
    }
    transport = ScriptedTransport(reply)
    bus = RecordingBus()
    out_dir = tmp_path / "images"
    client = MuseImageClient(transport, clock=FakeClock(), bus=bus, output_dir=out_dir)

    result = client.generate("Industrial kitchen sink area", size="1024x1024")

    assert isinstance(result, ImageResult)
    assert result.response_id == "img_resp_123"
    assert result.path.exists()
    assert result.path.read_bytes() == fake_png

    # Check relay telemetry
    assert len(bus.published) == 1
    topic, event = bus.published[0]
    assert topic == Topic.RELAY_MODEL_USED
    assert isinstance(event, ModelUsed)
    assert event.model == "muse-image"
    assert event.segment == "image"


def test_more_than_10_references_raises(tmp_path: Path) -> None:
    transport = ScriptedTransport()
    client = MuseImageClient(transport, clock=FakeClock(), output_dir=tmp_path)

    # Create 11 dummy reference image files
    refs = []
    for i in range(11):
        f = tmp_path / f"ref_{i}.png"
        f.write_bytes(b"dummy")
        refs.append(f)

    with pytest.raises(ValueError, match="At most 10 reference images allowed"):
        client.generate("Kitchen layout", reference_images=refs)


def test_mask_parameter_raises(tmp_path: Path) -> None:
    transport = ScriptedTransport()
    client = MuseImageClient(transport, clock=FakeClock(), output_dir=tmp_path)

    with pytest.raises(ValueError, match="does not support masks or inpainting"):
        client.generate("Kitchen layout", mask="mask.png")

    with pytest.raises(ValueError, match="does not support masks or inpainting"):
        client.generate("Kitchen layout", inpainting=True)


def test_rpm_limiter_spaces_calls(tmp_path: Path) -> None:
    fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake_image_content"
    encoded_png = base64.b64encode(fake_png).decode("ascii")
    replies = [
        {
            "status": 200,
            "headers": {},
            "json": {"id": f"img_{i}", "data": [{"b64_json": encoded_png}]},
        }
        for i in range(3)
    ]
    transport = ScriptedTransport(*replies)
    sleeps: list[float] = []
    clock = MonotonicClock()

    def mock_sleep(dt: float) -> None:
        sleeps.append(dt)
        clock.advance(dt)

    client = MuseImageClient(
        transport,
        clock=FakeClock(),
        output_dir=tmp_path,
        rpm=150.0,
        sleep=mock_sleep,
        monotonic=clock.time,
    )

    client.generate("Prompt 1")
    client.generate("Prompt 2")
    client.generate("Prompt 3")

    # At 150 RPM, each call after capacity is depleted gets spaced
    assert len(sleeps) >= 1
    for s in sleeps:
        assert s > 0.0


def test_sequential_edits_via_previous_response_id(tmp_path: Path) -> None:
    reply = {
        "status": 200,
        "headers": {},
        "json": {"id": "img_seq_2", "b64_json": base64.b64encode(b"img2").decode("ascii")},
    }
    transport = ScriptedTransport(reply)
    client = MuseImageClient(transport, clock=FakeClock(), output_dir=tmp_path)

    result = client.generate("Add wet floor sign", previous_response_id="img_seq_1")
    assert result.response_id == "img_seq_2"
    req_json = cast(dict[str, Any], transport.requests[0]["json"])
    assert req_json["previous_response_id"] == "img_seq_1"


def test_replay_miss_raises_replay_miss(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = ReplayCache(tmp_path, "replay", clock=clock)
    raw_transport = ScriptedTransport({"status": 200, "headers": {}, "json": {"id": "img"}})
    cached_transport = cache.wrap_transport("meta", "muse-image", raw_transport)
    client = MuseImageClient(cached_transport, clock=clock, output_dir=tmp_path)

    with pytest.raises(ReplayMiss):
        client.generate("Unrecorded image prompt")


def test_from_settings(tmp_path: Path) -> None:
    settings_live = Settings(
        rulecam_mode="live",
        meta_api_base_url="https://api.meta.ai/v1",
        meta_api_key=SecretStr("image-secret"),
    )
    client_live = MuseImageClient.from_settings(
        settings_live, clock=FakeClock(), output_dir=tmp_path
    )
    assert isinstance(client_live, MuseImageClient)

    settings_missing = Settings(
        rulecam_mode="live",
        meta_api_base_url=None,
        meta_api_key=None,
    )
    with pytest.raises(ClientConfigError):
        MuseImageClient.from_settings(settings_missing, clock=FakeClock())
