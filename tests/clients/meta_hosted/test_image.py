"""Unit tests for the hosted Muse Image client (task P2-01). No network."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from rulecam.bus import Bus, Topic
from rulecam.clients.errors import ClientConfigError, ModelResponseError
from rulecam.clients.meta_hosted.image import MuseImageClient, parse_image
from rulecam.clients.relay import ModelUsed
from rulecam.config import Settings
from rulecam.replay.cache import ReplayCache
from rulecam.replay.errors import ReplayMiss
from tests.clients.meta_hosted.transport_fakes import ScriptedTransport
from tests.fakes import FakeClock

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake"


class RecordingBus(Bus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[Topic, object]] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.published.append((topic, payload))
        super().publish(topic, payload)


def image_reply(rid: str = "resp_img1") -> dict[str, Any]:
    """A reply in the documented shape: the image is an `image_generation_call` result."""
    return {
        "status": 200,
        "headers": {},
        "json": {
            "id": rid,
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": "Here it is"}]},
                {
                    "type": "image_generation_call",
                    "id": "ig_signed_1",
                    "status": "completed",
                    "result": base64.b64encode(FAKE_PNG).decode("ascii"),
                },
            ],
        },
    }


def _refs(tmp_path: Path, n: int) -> list[Path]:
    paths = []
    for i in range(n):
        path = tmp_path / f"ref{i}.png"
        path.write_bytes(FAKE_PNG + bytes([i]))
        paths.append(path)
    return paths


def test_generate_sends_responses_request_and_saves_png(tmp_path: Path) -> None:
    transport = ScriptedTransport(image_reply())
    bus = RecordingBus()
    client = MuseImageClient(transport, clock=FakeClock(), bus=bus, output_dir=tmp_path / "img")

    result = client.generate("kitchen sink, nobody washing", size="1280x720")

    assert result.response_id == "resp_img1"
    assert result.path == tmp_path / "img" / "resp_img1.png"
    assert result.path.read_bytes() == FAKE_PNG
    [request] = transport.requests
    assert (request["method"], request["path"]) == ("POST", "/responses")
    body = request["json"]
    assert isinstance(body, dict)
    assert body["model"] == "muse-image-1.0"
    assert body["stream"] is False
    assert body["tools"] == [
        {"type": "image_generation", "size": "1280x720", "output_format": "png"}
    ]
    assert body["input"][0]["content"] == [
        {"type": "input_text", "text": "kitchen sink, nobody washing"}
    ]
    [(_, event)] = bus.published
    assert isinstance(event, ModelUsed)
    assert event.model == "muse-image-1.0"


def test_reference_images_are_uploaded_and_referenced(tmp_path: Path) -> None:
    transport = ScriptedTransport(image_reply())
    client = MuseImageClient(transport, clock=FakeClock(), output_dir=tmp_path)

    client.generate("same scene, no hard hat", reference_images=_refs(tmp_path, 2))

    assert len(transport.uploads) == 2
    assert transport.uploads[0]["file"]["content_type"] == "image/png"
    content = transport.bodies()[0]["input"][0]["content"]
    assert content[1:] == [{"type": "input_image", "file_id": "file-rec-0001"}] * 2


def test_more_than_10_references_raises(tmp_path: Path) -> None:
    transport = ScriptedTransport()
    client = MuseImageClient(transport, clock=FakeClock(), output_dir=tmp_path)
    with pytest.raises(ValueError, match="at most 10"):
        client.generate("p", reference_images=_refs(tmp_path, 11))
    assert transport.requests == []


@pytest.mark.parametrize("key", ["mask", "mask_image", "inpaint", "inpainting_strength"])
def test_mask_parameter_raises(tmp_path: Path, key: str) -> None:
    transport = ScriptedTransport()
    client = MuseImageClient(transport, clock=FakeClock(), output_dir=tmp_path)
    with pytest.raises(ValueError, match="no masks or inpainting"):
        client.generate("p", **{key: "x"})  # type: ignore[arg-type]
    assert transport.requests == []


def test_unknown_argument_and_bad_size_raise(tmp_path: Path) -> None:
    client = MuseImageClient(ScriptedTransport(), clock=FakeClock(), output_dir=tmp_path)
    with pytest.raises(ValueError, match="unsupported"):
        client.generate("p", quality="hd")
    with pytest.raises(ValueError, match="WxH"):
        client.generate("p", size="square")


def test_rpm_limiter_spaces_calls(tmp_path: Path) -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    transport = ScriptedTransport(*(image_reply(f"r{i}") for i in range(4)))
    client = MuseImageClient(
        transport, clock=FakeClock(), output_dir=tmp_path, sleep=sleep, monotonic=lambda: now[0]
    )
    for i in range(4):
        client.generate(f"prompt {i}")

    # 150 RPM is 2.5 per second: a burst of 2.5 tokens, then one token every 0.4 s.
    assert sleeps
    assert now[0] == pytest.approx(0.6, abs=0.05)
    assert all(s <= 0.4 + 1e-9 for s in sleeps)


def test_sequential_edits_via_previous_response_id(tmp_path: Path) -> None:
    transport = ScriptedTransport(image_reply("resp_a"), image_reply("resp_b"))
    client = MuseImageClient(transport, clock=FakeClock(), output_dir=tmp_path)

    first = client.generate("scene")
    second = client.generate("now add a forklift", previous_response_id=first.response_id)

    assert second.response_id == "resp_b"
    first_body, second_body = transport.bodies()
    assert "previous_response_id" not in first_body
    assert second_body["previous_response_id"] == "resp_a"


def test_reply_without_image_raises() -> None:
    with pytest.raises(ModelResponseError, match="image_generation_call"):
        parse_image({"id": "r", "output": [{"type": "message", "content": []}]})
    with pytest.raises(ModelResponseError, match="usable id"):
        parse_image({"output": []})
    with pytest.raises(ModelResponseError, match="base64"):
        parse_image({"id": "r", "output": [{"type": "image_generation_call", "result": "@@"}]})


def test_unsafe_response_id_is_sanitised_for_the_file_name(tmp_path: Path) -> None:
    transport = ScriptedTransport(image_reply("resp/../x.y"))
    result = MuseImageClient(transport, clock=FakeClock(), output_dir=tmp_path).generate("p")
    assert result.path == tmp_path / "resp____x_y.png"
    assert result.response_id == "resp/../x.y"


def test_replay_miss_raises_replay_miss(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = ReplayCache(tmp_path, "replay", clock=clock)
    inner = ScriptedTransport()
    client = MuseImageClient(
        cache.wrap_transport("meta", "muse-image-1.0", inner), clock=clock, output_dir=tmp_path
    )
    with pytest.raises(ReplayMiss):
        client.generate("p")
    assert inner.requests == []


def test_from_settings_saves_under_data_images(tmp_path: Path) -> None:
    live = Settings(
        rulecam_mode="live",
        meta_api_base_url="https://api.meta.ai/v1",
        meta_api_key=SecretStr("k"),
        rulecam_data_dir=tmp_path,
    )
    assert isinstance(MuseImageClient.from_settings(live, clock=FakeClock()), MuseImageClient)
    with pytest.raises(ClientConfigError):
        MuseImageClient.from_settings(
            Settings(rulecam_mode="live", meta_api_base_url=None, meta_api_key=None),
            clock=FakeClock(),
        )
