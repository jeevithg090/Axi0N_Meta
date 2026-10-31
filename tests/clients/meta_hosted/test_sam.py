"""HostedSam31 against recorded-style fixtures through an injected transport (no network)."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Mapping
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from rulecam.bus import Bus, Topic
from rulecam.clients.errors import ClientConfigError, ModelHTTPError, ModelResponseError
from rulecam.clients.meta_hosted.sam import HostedSam31, build_prompts
from rulecam.clients.meta_hosted.sam_cost import USD_PER_VIDEO_FRAME
from rulecam.clients.meta_hosted.sam_wire import MaskDecoder, ObjectPrompt, prompt_text
from rulecam.clients.relay import ModelUsed
from rulecam.clients.transport import RetryPolicy
from rulecam.config import Settings
from rulecam.contracts import Chunk
from rulecam.perception.masks import NO_MASK, decode_rle, encode_rle
from tests.clients.meta_hosted.transport_fakes import ScriptedTransport, load_reply, ones_decoder
from tests.fakes import EPOCH, FakeClock


class RecordingBus(Bus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[Topic, object]] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.published.append((topic, payload))
        super().publish(topic, payload)


class Ticker:
    """A monotonic clock that advances 0.1 s per reading."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.1
        return self.now


@pytest.fixture
def chunk(tmp_path: Path) -> Chunk:
    path = tmp_path / "cam1_000000.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypisom-recorded-chunk")
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


@pytest.fixture
def sleeps() -> list[float]:
    return []


Built = tuple[HostedSam31, ScriptedTransport, RecordingBus]
Factory = Callable[..., Built]


@pytest.fixture
def make_client(sleeps: list[float]) -> Factory:
    def build(
        *replies: dict[str, object], mask_decoder: MaskDecoder | None = ones_decoder
    ) -> Built:
        transport = ScriptedTransport(*replies)
        bus = RecordingBus()
        client = HostedSam31(
            transport,
            clock=FakeClock(),
            bus=bus,
            sleep=sleeps.append,
            monotonic=Ticker(),
            seed=7,
            mask_decoder=mask_decoder,
        )
        return client, transport, bus

    return build


def test_normal_response_parses_masks_boxes_and_confidence(
    make_client: Factory, chunk: Chunk
) -> None:
    client, transport, _ = make_client(load_reply("video_masks"))

    detections = client.segment(chunk, "person")

    assert [(d.frame_idx, d.object_id) for d in detections] == [(0, 0), (0, 1), (1, 0)]
    first = detections[0]
    assert first.concept == "person"
    assert first.camera_id == "cam1"
    assert first.ts == EPOCH
    assert detections[2].ts == EPOCH + timedelta(seconds=0.1)
    assert first.mask.box_xyxy == (10.0, 20.0, 19.0, 59.0)
    assert first.mask.confidence == pytest.approx(0.95)
    raster = decode_rle(first.mask.rle)
    assert raster.shape == (80, 64)
    assert raster[20:60, 10:20].all()
    assert raster.sum() == 40 * 10
    body = transport.bodies()[0]
    assert body["model"] == "sam-3.1"
    assert body["stream"] is False
    assert body["input"][0]["content"] == [
        {"type": "input_text", "text": "person"},
        {"type": "input_video", "file_id": "file-rec-0001"},
    ]
    assert body["metadata"] == {"include_confidence": "true"}


def test_upload_is_sent_once_per_chunk_content(make_client: Factory, chunk: Chunk) -> None:
    client, transport, _ = make_client(load_reply("video_empty"), load_reply("video_empty"))

    client.segment(chunk, "person")
    client.segment(chunk, "hard hat")

    assert len(transport.uploads) == 1
    upload = transport.uploads[0]
    assert upload["form"] == {"purpose": "user_data"}
    assert upload["file"]["content_type"] == "video/mp4"


def test_empty_completion_returns_empty_list(make_client: Factory, chunk: Chunk) -> None:
    client, _, _ = make_client(load_reply("video_empty"))

    assert client.segment(chunk, "forklift") == []


def test_out_of_order_frames_come_back_sorted(make_client: Factory, chunk: Chunk) -> None:
    client, _, _ = make_client(load_reply("video_out_of_order"))

    detections = client.segment(chunk, "person", box_prompts={3: (10, 20, 19, 59)})

    assert [d.frame_idx for d in detections] == [0, 1, 2]
    assert {d.object_id for d in detections} == {3}


def test_429_then_success_retries_with_backoff(
    make_client: Factory, chunk: Chunk, sleeps: list[float]
) -> None:
    client, transport, bus = make_client(load_reply("rate_limited"), load_reply("video_masks"))

    detections = client.segment(chunk, "person")

    assert len(detections) == 3
    assert len(transport.segment_requests) == 2
    assert sleeps[-1] >= 1.0  # honours Retry-After: 1
    event = next(p for t, p in bus.published if t is Topic.RELAY_MODEL_USED)
    assert isinstance(event, ModelUsed)
    assert event.attempts == 2


def test_gives_up_after_four_tries(make_client: Factory, chunk: Chunk) -> None:
    client, transport, _ = make_client(*[load_reply("rate_limited")] * 4)

    with pytest.raises(ModelHTTPError) as raised:
        client.segment(chunk, "person")

    assert raised.value.status == 429
    assert raised.value.attempts == 4
    assert len(transport.segment_requests) == 4


def test_server_errors_are_retried_but_400_is_not(make_client: Factory, chunk: Chunk) -> None:
    server_error = {"status": 503, "headers": {}, "json": {"error": "overloaded"}}
    bad_request = {"status": 400, "headers": {}, "json": {"error": {"message": "bad prompt"}}}
    client, transport, _ = make_client(server_error, load_reply("video_empty"), bad_request)

    assert client.segment(chunk, "person") == []
    with pytest.raises(ModelHTTPError, match="bad prompt"):
        client.segment(chunk, "person")
    assert len(transport.segment_requests) == 3


def test_text_plus_point_prompt_raises_before_any_http_call(
    make_client: Factory, chunk: Chunk
) -> None:
    client, transport, _ = make_client()

    with pytest.raises(ValueError, match="cannot combine"):
        client.segment(chunk, "harness", negative_points=[(5, 10.0, 12.0)])

    assert transport.requests == []


def test_text_plus_box_prompt_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot combine"):
        ObjectPrompt(text="person", boxes={1: (0, 0, 5, 5)})


def test_twenty_box_prompts_are_split_into_16_and_4(make_client: Factory, chunk: Chunk) -> None:
    client, transport, bus = make_client(load_reply("video_empty"), load_reply("video_empty"))
    boxes = {i: (float(i), 0.0, float(i) + 2, 4.0) for i in range(20)}

    client.segment(chunk, "pallet", box_prompts=boxes)

    texts = transport.prompt_texts()
    assert [t.count("<|box;") for t in texts] == [16, 4]
    assert texts[0].startswith("<0f>0<|box;x1=0;y1=0;x2=2;y2=4;w=64;h=80|>,1<|box;")
    assert texts[1].startswith("<0f>16<|box;")
    metadata = transport.bodies()[0]["metadata"]
    assert metadata["propagation_direction"] == "forward"
    relay = [p for t, p in bus.published if t is Topic.RELAY_MODEL_USED]
    assert len(relay) == 2


def test_backward_box_prompt_goes_on_last_frame_with_negative_point(
    make_client: Factory, chunk: Chunk
) -> None:
    client, transport, _ = make_client(load_reply("video_empty"))

    client.segment(
        chunk,
        "harness",
        box_prompts={1: (10, 20, 19, 59)},
        negative_points=[(29, 15.0, 30.0), (12, 14.0, 31.0)],
        direction="backward",
        include_confidence=False,
    )

    body = transport.bodies()[0]
    assert body["metadata"] == {
        "include_confidence": "false",
        "propagation_direction": "backward",
    }
    assert body["input"][0]["content"][0]["text"] == (
        "<12f>1-<|point;x=14;y=31;w=64;h=80|>"
        "<29f>1<|box;x1=10;y1=20;x2=19;y2=59;w=64;h=80|>-<|point;x=15;y=30;w=64;h=80|>"
    )


def test_box_coordinates_are_clamped_to_the_frame(chunk: Chunk) -> None:
    prompts = build_prompts(chunk, "person", {1: (-5.0, -1.0, 99.0, 200.0)}, None, "forward")

    assert prompt_text(prompts[0], 64, 80) == "<0f>1<|box;x1=0;y1=0;x2=63;y2=79;w=64;h=80|>"


def test_thirty_frame_request_is_billed_at_fifty_frames(make_client: Factory, chunk: Chunk) -> None:
    client, _, _ = make_client(load_reply("video_masks"))

    client.segment(chunk, "person")

    cost = client.costs.snapshot()["cam1"]
    assert cost.video_requests == 1
    assert cost.billed_frames == 50
    assert cost.usd == pytest.approx(50 * USD_PER_VIDEO_FRAME)
    assert client.costs.total_usd() == pytest.approx(0.01)


def test_relay_model_used_is_published_with_latency(make_client: Factory, chunk: Chunk) -> None:
    client, _, bus = make_client(load_reply("video_masks"))

    client.segment(chunk, "person")

    relay = [p for t, p in bus.published if t is Topic.RELAY_MODEL_USED]
    assert len(relay) == 1
    event = relay[0]
    assert isinstance(event, ModelUsed)
    assert (event.model, event.segment, event.camera_id, event.attempts) == (
        "sam-3.1",
        "perception",
        "cam1",
        1,
    )
    assert event.latency_ms > 0  # measured on the injected monotonic clock


def test_without_a_decoder_masks_fall_back_to_boxes(make_client: Factory, chunk: Chunk) -> None:
    client, _, _ = make_client(load_reply("video_masks"), mask_decoder=None)

    detections = client.segment(chunk, "person")

    assert {d.mask.rle for d in detections} == {NO_MASK}
    assert detections[0].mask.box_xyxy == (10.0, 20.0, 19.0, 59.0)


def test_include_confidence_false_drops_confidences(make_client: Factory, chunk: Chunk) -> None:
    client, transport, _ = make_client(load_reply("video_masks"))

    detections = client.segment(chunk, "person", include_confidence=False)

    assert {d.mask.confidence for d in detections} == {None}
    assert transport.bodies()[0]["metadata"]["include_confidence"] == "false"


def test_segment_concepts_runs_one_request_per_concept(make_client: Factory, chunk: Chunk) -> None:
    client, transport, bus = make_client(load_reply("video_empty"), load_reply("video_empty"))

    results = asyncio.run(client.segment_concepts(chunk, ["person", "hard hat", "person"]))

    assert set(results) == {"person", "hard hat"}
    assert sorted(transport.prompt_texts()) == ["hard hat", "person"]
    assert len([t for t, _ in bus.published if t is Topic.RELAY_MODEL_USED]) == 2


def test_segment_image_probe(make_client: Factory, tmp_path: Path) -> None:
    client, transport, bus = make_client(load_reply("image_one"))
    frame = tmp_path / "probe.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    detections = client.segment_image(frame, "sink", camera_id="cam2")

    assert len(detections) == 1
    assert detections[0].ts == EPOCH
    assert detections[0].frame_idx == 0
    assert decode_rle(detections[0].mask.rle)[3:8, 2:6].all()
    content = transport.bodies()[0]["input"][0]["content"]
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert client.costs.snapshot()["cam2"].images == 1
    assert client.costs.snapshot()["cam2"].usd == pytest.approx(0.0025)
    assert any(t is Topic.RELAY_MODEL_USED for t, _ in bus.published)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"path": "data/cam1.webm"}, "MP4 or MOV"),
        ({"n_frames": 15_001}, "at most 15000 frames"),
    ],
)
def test_unsendable_chunks_raise_before_any_call(
    make_client: Factory, chunk: Chunk, change: dict[str, object], message: str
) -> None:
    client, transport, _ = make_client()

    with pytest.raises(ValueError, match=message):
        client.segment(chunk.model_copy(update=change), "person")
    assert transport.requests == []


def test_backward_needs_more_than_one_frame(make_client: Factory, chunk: Chunk) -> None:
    client, _, _ = make_client()

    with pytest.raises(ValueError, match="after frame 0"):
        client.segment(
            chunk.model_copy(update={"n_frames": 1}),
            "person",
            box_prompts={1: (0, 0, 4, 4)},
            direction="backward",
        )


def test_segment_image_rejects_unknown_types(make_client: Factory, tmp_path: Path) -> None:
    client, _, _ = make_client()

    with pytest.raises(ValueError, match="JPEG or PNG"):
        client.segment_image(tmp_path / "frame.gif", "sink")


def test_upload_without_id_is_a_response_error(chunk: Chunk) -> None:
    def transport(request: Mapping[str, object]) -> Mapping[str, object]:
        return {"status": 200, "headers": {}, "json": {"status": "processed"}}

    client = HostedSam31(transport, clock=FakeClock(), retry=RetryPolicy(max_attempts=1))

    with pytest.raises(ModelResponseError, match="no id"):
        client.segment(chunk, "person")


def test_from_settings_needs_keys_outside_replay() -> None:
    with pytest.raises(ClientConfigError, match="META_API_KEY"):
        HostedSam31.from_settings(
            Settings(rulecam_mode="live", meta_api_key=None), clock=FakeClock()
        )


def test_from_settings_builds_a_live_client() -> None:
    settings = Settings(
        rulecam_mode="live", meta_api_base_url="https://api.example.test/v1", meta_api_key="k"
    )

    assert isinstance(HostedSam31.from_settings(settings, clock=FakeClock()), HostedSam31)


def test_rle_round_trips_random_masks() -> None:
    rng = np.random.default_rng(3)
    for shape in [(1, 1), (7, 5), (80, 64)]:
        mask = (rng.random(shape) > 0.6).astype(np.uint8)
        assert (decode_rle(encode_rle(mask)) == mask).all()


def _gated_upload_transport(
    started: list[str], release: threading.Event
) -> Callable[[Mapping[str, object]], Mapping[str, object]]:
    """Uploads block until released; everything else returns an empty video reply."""
    empty = load_reply("video_empty")

    def transport(request: Mapping[str, object]) -> Mapping[str, object]:
        if request.get("path") == "/files":
            file = request["file"]
            assert isinstance(file, Mapping)
            started.append(str(file["filename"]))
            assert release.wait(5)
            return {"status": 200, "headers": {}, "json": {"id": f"file-{file['filename']}"}}
        return empty

    return transport


def _chunk_at(tmp_path: Path, name: str, content: bytes) -> Chunk:
    path = tmp_path / f"{name}.mp4"
    path.write_bytes(content)
    return Chunk(
        chunk_id=name,
        camera_id="cam1",
        path=str(path),
        start_ts=EPOCH,
        fps=10,
        n_frames=30,
        width=64,
        height=48,
    )


def test_different_chunks_upload_in_parallel(tmp_path: Path) -> None:
    started: list[str] = []
    release = threading.Event()
    client = HostedSam31(_gated_upload_transport(started, release), clock=FakeClock())
    chunks = [_chunk_at(tmp_path, f"c{i}", f"video-{i}".encode()) for i in range(3)]
    threads = [threading.Thread(target=client.segment, args=(c, "person")) for c in chunks]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 5
    while len(started) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sorted(started) == ["c0.mp4", "c1.mp4", "c2.mp4"]  # all three in flight at once
    release.set()
    for thread in threads:
        thread.join(5)


def test_same_chunk_concurrent_concepts_share_one_upload(tmp_path: Path) -> None:
    started: list[str] = []
    release = threading.Event()
    client = HostedSam31(_gated_upload_transport(started, release), clock=FakeClock())
    chunk = _chunk_at(tmp_path, "c0", b"same-video")
    threads = [
        threading.Thread(target=client.segment, args=(chunk, concept))
        for concept in ("person", "forklift", "pallet")
    ]
    for thread in threads:
        thread.start()
    time.sleep(0.2)
    release.set()
    for thread in threads:
        thread.join(5)
    assert started == ["c0.mp4"]


def test_runtime_view_returns_boxes_without_decoding_and_shares_caches(chunk: Chunk) -> None:
    decoded: list[object] = []

    def counting_decoder(mask: object) -> np.ndarray:
        decoded.append(mask)
        return ones_decoder(mask)  # type: ignore[arg-type]

    transport = ScriptedTransport(load_reply("video_masks"), load_reply("video_masks"))
    client = HostedSam31(transport, clock=FakeClock(), mask_decoder=counting_decoder)
    runtime = client.runtime_view()
    stages: list[tuple[str, str, str]] = []
    runtime.set_stage_sink(
        lambda stage, camera, chunk_id, s: stages.append((stage, camera, chunk_id))
    )

    boxes = runtime.segment(chunk, "person")
    full = client.segment(chunk, "person")

    assert {d.mask.rle for d in boxes} == {NO_MASK}
    assert [d.mask.box_xyxy for d in boxes] == [d.mask.box_xyxy for d in full]
    assert len(decoded) == len(full)  # only the full client decoded
    assert len(transport.uploads) == 1  # one upload shared by both
    assert client.costs is runtime.costs
    # Only the runtime view reports stages: its upload and its one request.
    assert stages == [("upload", "cam1", "cam1_000000"), ("sam_request", "cam1", "cam1_000000")]


def test_runtime_view_can_keep_masks_and_the_sink_can_be_removed(chunk: Chunk) -> None:
    transport = ScriptedTransport(load_reply("video_masks"))
    runtime = HostedSam31(transport, clock=FakeClock(), mask_decoder=ones_decoder).runtime_view(
        masks=True
    )
    stages: list[str] = []
    runtime.set_stage_sink(lambda stage, *_: stages.append(stage))
    runtime.set_stage_sink(None)
    detections = runtime.segment(chunk, "person")
    assert NO_MASK not in {d.mask.rle for d in detections}
    assert stages == []
