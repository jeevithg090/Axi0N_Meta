"""HostedSam31 through the replay cache (P3-02 integration, issue #7); no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rulecam.bus import Bus, Topic
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted.sam import HostedSam31, _recorded_latency_ms
from rulecam.clients.meta_hosted.sam_wire import SAM_MODEL
from rulecam.clients.relay import ModelUsed
from rulecam.clients.transport import RetryPolicy
from rulecam.config import Settings
from rulecam.contracts import Chunk
from rulecam.replay.cache import ReplayCache
from rulecam.replay.errors import ReplayMiss
from tests.clients.meta_hosted.transport_fakes import ScriptedTransport, load_reply, ones_decoder
from tests.fakes import EPOCH, FakeClock

RECORDED_LATENCY_MS = 1234


class RecordingBus(Bus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[Topic, object]] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.published.append((topic, payload))
        super().publish(topic, payload)


def make_chunk(tmp_path: Path, content: bytes = b"\x00\x00\x00\x18ftypisom-chunk") -> Chunk:
    path = tmp_path / "cam1_000000.mp4"
    path.write_bytes(content)
    return Chunk(
        chunk_id="cam1_000000",
        camera_id="cam1",
        path=str(path),
        start_ts=EPOCH,
        fps=10.0,
        n_frames=30,
        width=64,
        height=80,
    )


def replay_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        rulecam_mode="replay",
        rulecam_replay_dir=tmp_path / "replay",
        meta_api_key=None,
        meta_api_base_url=None,
    )


def record(tmp_path: Path, chunk: Chunk, *replies: dict[str, object]) -> ReplayCache:
    """Record one segment call through the cache, then pin every entry's latency."""
    cache = ReplayCache(tmp_path / "replay", "record", clock=FakeClock())
    transport = cache.wrap_transport("meta", SAM_MODEL, ScriptedTransport(*replies))
    client = HostedSam31(
        transport,
        clock=FakeClock(),
        mask_decoder=ones_decoder,
        sleep=lambda s: None,
        retry=RetryPolicy(max_attempts=3),
    )
    client.segment(chunk, "person")
    for entry in (tmp_path / "replay" / "meta").glob("*.json"):
        data = json.loads(entry.read_text(encoding="utf-8"))
        data["latency_ms"] = RECORDED_LATENCY_MS
        entry.write_text(json.dumps(data), encoding="utf-8")
    cache.set_mode("replay")
    return cache


def test_replay_needs_no_key_and_serves_recorded_replies(tmp_path: Path) -> None:
    chunk = make_chunk(tmp_path)
    cache = record(tmp_path, chunk, load_reply("video_empty"))
    bus = RecordingBus()
    client = HostedSam31.from_settings(
        replay_settings(tmp_path), clock=FakeClock(), bus=bus, cache=cache
    )

    assert client.segment(chunk, "person") == []
    event = next(p for t, p in bus.published if t is Topic.RELAY_MODEL_USED)
    assert isinstance(event, ModelUsed)
    assert event.latency_ms == RECORDED_LATENCY_MS


def test_replay_miss_raises_and_never_calls_the_network(tmp_path: Path) -> None:
    client = HostedSam31.from_settings(replay_settings(tmp_path), clock=FakeClock())
    with pytest.raises(ReplayMiss):
        client.segment(make_chunk(tmp_path, b"never recorded"), "person")


def test_failed_replies_are_never_recorded(tmp_path: Path) -> None:
    chunk = make_chunk(tmp_path)
    cache = record(tmp_path, chunk, load_reply("rate_limited"), load_reply("video_empty"))
    entries = [json.loads(p.read_text()) for p in (tmp_path / "replay" / "meta").glob("*.json")]
    assert entries
    assert all(e["response"]["status"] == 200 for e in entries)
    client = HostedSam31.from_settings(replay_settings(tmp_path), clock=FakeClock(), cache=cache)
    assert client.segment(chunk, "person") == []


def test_switching_a_keyless_replay_client_to_live_fails_loudly(tmp_path: Path) -> None:
    cache = ReplayCache(tmp_path / "replay", "replay", clock=FakeClock())
    client = HostedSam31.from_settings(replay_settings(tmp_path), clock=FakeClock(), cache=cache)
    cache.set_mode("live")
    with pytest.raises(ClientConfigError, match="no Meta API credentials"):
        client.segment(make_chunk(tmp_path), "person")


def test_from_settings_builds_its_own_cache_from_settings(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rulecam_mode="record",
        rulecam_replay_dir=tmp_path / "replay",
        meta_api_base_url="https://api.example.test/v1",
        meta_api_key="k",
    )
    assert isinstance(HostedSam31.from_settings(settings, clock=FakeClock()), HostedSam31)


@pytest.mark.parametrize(("value", "parsed"), [(None, None), ("17", 17), ("-3", 0), ("x", None)])
def test_recorded_latency_header_parsing(value: str | None, parsed: int | None) -> None:
    assert _recorded_latency_ms(value) == parsed
