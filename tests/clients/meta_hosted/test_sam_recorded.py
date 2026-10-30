"""HostedSam31 against REAL SAM 3.1 replies recorded on Oct 30 2026 (issue #5).

The clip was `ffmpeg -f lavfi -i testsrc2=size=640x360:rate=10 -t 3` (30 frames). Replies are
verbatim in `tests/fixtures/replay/sam/live/`; requests are trimmed (no media bytes).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from rulecam.clients.errors import ModelHTTPError
from rulecam.clients.meta_hosted.sam import HostedSam31
from rulecam.clients.meta_hosted.sam_wire import WireMask, output_text, parse_output
from rulecam.contracts import Chunk
from tests.clients.meta_hosted.transport_fakes import SAM_FIXTURES
from tests.fakes import EPOCH, FakeClock

LIVE = SAM_FIXTURES / "live"
RECTANGLE_BOX = (122.0, 262.0, 197.0, 277.0)


def recorded(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((LIVE / f"{name}.json").read_text("utf-8"))
    return data


class RecordedTransport:
    """Serves the recorded upload for `/files` and the given recorded replies in order."""

    def __init__(self, *names: str) -> None:
        self._replies = [recorded(n) for n in names]
        self.requests: list[Mapping[str, object]] = []

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        self.requests.append(request)
        if request["path"] == "/files":
            return recorded("upload")
        return self._replies.pop(0)

    def body(self, index: int) -> dict[str, Any]:
        bodies = [r["json"] for r in self.requests if r["path"] == "/responses"]
        body = bodies[index]
        assert isinstance(body, dict)
        return body


class ShapeDecoder:
    """Records each mask token and returns an all-foreground raster of its size."""

    def __init__(self) -> None:
        self.masks: list[WireMask] = []

    def __call__(self, mask: WireMask) -> NDArray[np.uint8]:
        self.masks.append(mask)
        return np.ones((mask.height, mask.width), dtype=np.uint8)


@pytest.fixture
def scene(tmp_path: Path) -> Chunk:
    path = tmp_path / "scene.mp4"
    path.write_bytes(b"\x00\x00\x00 ftypisom-testsrc2-640x360")
    return Chunk(
        chunk_id="scene",
        camera_id="live",
        path=str(path),
        start_ts=EPOCH,
        fps=10.0,
        n_frames=30,
        width=640,
        height=360,
    )


def _unsorted(reply: dict[str, Any]) -> list[Any]:
    """Records in arrival order (one parse per line keeps the line order)."""
    return [r for line in output_text(reply["json"]).splitlines() for r in parse_output(line)]


def client(transport: RecordedTransport, decoder: ShapeDecoder | None = None) -> HostedSam31:
    return HostedSam31(
        transport, clock=FakeClock(), sleep=lambda s: None, seed=0, mask_decoder=decoder
    )


def test_rectangle_reply_parses_with_the_documented_geometry(scene: Chunk) -> None:
    decoder = ShapeDecoder()
    transport = RecordedTransport("video_rectangle")

    detections = client(transport, decoder).segment(scene, "rectangle")

    first = detections[0]
    assert (first.frame_idx, first.object_id, first.mask.box_xyxy) == (2, 0, RECTANGLE_BOX)
    assert first.mask.confidence == pytest.approx(0.7104072570800781)
    assert (decoder.masks[0].height, decoder.masks[0].width) == (16, 76)
    assert decoder.masks[0].encoding == "lossless"
    assert [d.object_id for d in detections if d.frame_idx == 2] == [0]
    assert len(detections) == 46
    assert {d.object_id for d in detections} == {0, 1}
    assert [d.frame_idx for d in detections] == sorted(d.frame_idx for d in detections)


def test_every_recorded_mask_covers_its_box_inclusively() -> None:
    names = ["video_rectangle", "video_number", "video_backward_negative_point"]
    records = [r for n in names for r in parse_output(output_text(recorded(n)["json"]))]

    assert len(records) == 46 + 37 + 20
    for r in records:
        x1, y1, x2, y2 = r.box_xyxy
        assert (r.mask.height, r.mask.width) == (y2 - y1 + 1, x2 - x1 + 1)
        assert (r.frame_width, r.frame_height) == (640, 360)


def test_number_reply_gives_37_detections(scene: Chunk) -> None:
    detections = client(RecordedTransport("video_number")).segment(scene, "number")

    assert len(detections) == 37


@pytest.mark.parametrize("name", ["video_circle_empty", "video_colored_square_empty"])
def test_empty_completions_return_empty_lists(scene: Chunk, name: str) -> None:
    sam = client(RecordedTransport(name))

    assert sam.segment(scene, "circle") == []
    assert sam.costs.snapshot()["live"].billed_frames == 50


def test_requests_match_the_recorded_ones(scene: Chunk) -> None:
    transport = RecordedTransport("video_rectangle")

    client(transport).segment(scene, "rectangle")

    assert transport.body(0) == recorded("video_rectangle")["request"]["json"]


def test_backward_box_prompt_with_negative_point_matches_the_recording(scene: Chunk) -> None:
    transport = RecordedTransport("video_backward_negative_point")

    detections = client(transport).segment(
        scene,
        "rectangle",
        box_prompts={1: RECTANGLE_BOX},
        negative_points=[(29, 130.0, 270.0)],
        direction="backward",
    )

    sent = transport.body(0)
    recorded_input = recorded("video_backward_negative_point")["request"]["json"]["input"]
    assert sent["input"][0]["content"][0] == recorded_input[0]["content"][0]  # same prompt text
    assert sent["metadata"]["propagation_direction"] == "backward"
    arrival = [r.frame_idx for r in _unsorted(recorded("video_backward_negative_point"))]
    assert arrival[:3] == [29, 28, 27]  # backward tracking returns frames in reverse
    assert len(detections) == 20
    assert [d.frame_idx for d in detections] == sorted(arrival)
    assert {d.object_id for d in detections} == {1}


def test_thirty_frame_chunks_bill_fifty_frames_per_request(scene: Chunk) -> None:
    sam = client(RecordedTransport("video_rectangle", "video_number", "video_circle_empty"))

    for concept in ["rectangle", "number", "circle"]:
        sam.segment(scene, concept)

    cost = sam.costs.snapshot()["live"]
    assert (cost.video_requests, cost.billed_frames) == (3, 150)
    assert cost.usd == pytest.approx(0.03)


def test_image_probe_reply(tmp_path: Path) -> None:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\n")
    sam = client(RecordedTransport("image_rectangle_empty"))

    assert sam.segment_image(frame, "rectangle", camera_id="live") == []
    assert sam.costs.snapshot()["live"].images == 1


def test_recorded_400_raises_with_the_api_message(scene: Chunk) -> None:
    sam = client(RecordedTransport("error_400_no_media"))

    with pytest.raises(ModelHTTPError, match="requires an input_image or input_video") as raised:
        sam.segment(scene, "rectangle")

    assert (raised.value.status, raised.value.attempts) == (400, 1)
