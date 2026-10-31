"""`scripts/sam_pod/compare.py`: hosted vs self-hosted SAM report, with fake segmenters (P1-03)."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator, Mapping
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from rulecam.contracts.core import Chunk, Detection, Mask
from tests.fakes import FakeSegmenter, make_chunk

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sam_pod" / "compare.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sam_compare", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


compare = load_script()


def _detections(chunk: Chunk, concept: str, frames: list[int]) -> list[Detection]:
    return [
        Detection(
            camera_id=chunk.camera_id,
            frame_idx=f,
            ts=chunk.start_ts + timedelta(seconds=f / chunk.fps),
            concept=concept,
            object_id=1,
            mask=Mask(rle="", box_xyxy=(0.0, 0.0, 1.0, 1.0), confidence=0.9),
        )
        for f in frames
    ]


def _segmenter(frames: Mapping[str, list[int]]) -> FakeSegmenter:
    return FakeSegmenter(
        responder=lambda chunk, concept: _detections(chunk, concept, frames[concept])
    )


def _ticks(step: float = 0.25) -> Iterator[float]:
    value = 0.0
    while True:
        yield value
        value += step


def test_presence_agreement_counts_frames_where_both_agree() -> None:
    assert compare.presence_agreement(frozenset({0, 1}), frozenset({0, 1}), 10) == 1.0
    assert compare.presence_agreement(frozenset({0, 1, 2, 3}), frozenset(), 10) == pytest.approx(
        0.6
    )
    assert compare.presence_agreement(frozenset(), frozenset(), 0) == 1.0


def test_compare_reports_detections_frames_latency_and_agreement() -> None:
    chunk = make_chunk(n_frames=10)
    hosted = _segmenter({"forklift": [0, 1, 2, 3], "person": [0, 0, 5]})
    self_hosted = _segmenter({"forklift": [], "person": [0, 5]})
    ticks = _ticks()
    rows = compare.compare(
        [chunk], ["forklift", "person"], hosted, self_hosted, lambda: next(ticks)
    )

    forklift, person = rows
    assert (forklift.hosted.detections, len(forklift.hosted.frames)) == (4, 4)
    assert (forklift.self_hosted.detections, forklift.self_hosted.frames) == (0, frozenset())
    assert forklift.hosted.latency_ms == 250
    assert forklift.agreement == pytest.approx(0.6)
    assert person.hosted.detections == 3
    assert person.agreement == 1.0
    assert [c.concept for c in hosted.calls] == ["forklift", "person"]
    assert hosted.calls[0].direction == "forward"


def test_format_rows_prints_a_table_errors_and_a_summary() -> None:
    chunk = make_chunk(n_frames=10)

    def broken(_chunk: Chunk, _concept: str) -> list[Detection]:
        raise RuntimeError("pod down")

    rows = compare.compare(
        [chunk],
        ["forklift", "person"],
        _segmenter({"forklift": [0, 1, 2, 3], "person": [4]}),
        FakeSegmenter(
            responder=lambda c, k: broken(c, k) if k == "person" else _detections(c, k, [0, 1])
        ),
        lambda: 0.0,
    )
    text = compare.format_rows(rows)
    lines = text.splitlines()
    assert lines[0].split() == [
        "chunk",
        "concept",
        "frames",
        "h_det",
        "s_det",
        "h_frm",
        "s_frm",
        "h_ms",
        "s_ms",
        "agree",
    ]
    assert lines[1].split() == [
        "cam1_000000.mp4",
        "forklift",
        "10",
        "4",
        "2",
        "4",
        "2",
        "0",
        "0",
        "0.80",
    ]
    assert lines[2].split() == [
        "cam1_000000.mp4",
        "person",
        "10",
        "1",
        "ERR",
        "1",
        "ERR",
        "0",
        "0",
        "-",
    ]
    assert "  cam1_000000.mp4 person self-hosted: RuntimeError: pod down" in lines
    assert any(
        line.startswith("  forklift: hosted 4/10, self-hosted 2/10, agreement 0.80")
        for line in lines
    )
    assert any(
        line.startswith("  person: hosted 1/10, self-hosted 0/10, agreement -") for line in lines
    )
    assert compare.worst_agreement(rows) == 0.0


def test_format_memory_reads_the_health_memory() -> None:
    health = {
        "memory": {
            "allocated_bytes": 4 * 2**30,
            "reserved_bytes": 6 * 2**30,
            "sessions": 12,
            "guard_trips": 0,
        }
    }
    assert compare.format_memory("after", health) == (
        "self-hosted memory after: allocated 4.00 GiB, reserved 6.00 GiB, "
        "sessions 12, guard trips 0"
    )
    assert compare.format_memory("before", None) == "self-hosted memory before: unavailable"
    assert (
        compare.format_memory("before", {"memory": {}}) == "self-hosted memory before: unavailable"
    )


def test_main_prints_memory_table_and_gates_on_agreement(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    files = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    chunks = {
        path: make_chunk(index=i, n_frames=4).model_copy(update={"path": str(path)})
        for i, path in enumerate(files)
    }
    health: list[dict[str, Any]] = [
        {"memory": {"allocated_bytes": 4 * 2**30, "reserved_bytes": 5 * 2**30, "sessions": 0}},
        {"memory": {"allocated_bytes": 4 * 2**30, "reserved_bytes": 5 * 2**30, "sessions": 2}},
    ]

    def backends() -> tuple[Any, Any, Any]:
        return (
            _segmenter({"forklift": [0, 1, 2, 3]}),
            _segmenter({"forklift": [0, 1]}),
            lambda: health.pop(0) if health else None,
        )

    argv = [*map(str, files), "--concept", "forklift", "--min-agreement", "0.9"]
    assert compare.main(argv, backends=backends, probe=chunks.__getitem__) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("self-hosted memory before: allocated 4.00 GiB")
    assert out[2].split()[0] == "a.mp4"
    assert out[3].split()[0] == "b.mp4"
    assert out[-1].startswith("self-hosted memory after: allocated 4.00 GiB")
    assert "sessions 2" in out[-1]

    assert compare.main(argv[:-2], backends=backends, probe=chunks.__getitem__) == 0


def _load(name: str) -> ModuleType:
    path = SCRIPT.parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"sam_{name}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


soak = _load("soak")


def _health(allocated: list[float]) -> Any:
    values = list(allocated)

    def health() -> dict[str, Any]:
        gib = values.pop(0) if len(values) > 1 else values[0]
        return {
            "memory": {
                "allocated_bytes": int(gib * 2**30),
                "reserved_bytes": int((gib + 1) * 2**30),
                "peak_allocated_bytes": int((gib + 2) * 2**30),
                "guard_trips": 0,
            }
        }

    return health


def test_soak_body_is_a_forward_text_request() -> None:
    body = soak.segment_body(b"mp4", "forklift", 1280, 720, 23)
    assert body["video_b64"] == "bXA0"
    assert body["request"]["text"] == "forklift"
    assert body["request"]["direction"] == "forward"
    assert (body["request"]["width"], body["request"]["n_frames"]) == (1280, 23)


def test_soak_passes_when_memory_stays_flat() -> None:
    lines: list[str] = []
    code = soak.soak(
        3,
        {},
        lambda _body: (200, {"objects": [{}, {}]}),
        _health([4.0, 4.0, 4.1, 4.1]),
        max_growth_gb=0.5,
        out=lines.append,
        timer=lambda: 0.0,
    )
    assert code == 0
    assert lines[0] == "start alloc=4.00 reserved=5.00 peak=6.00 guard_trips=0"
    assert lines[1].startswith("call   1 status=200 objects=2 ms=0 alloc=4.00")
    assert lines[-1] == "PASS: 0 failed calls, allocated growth 0.10 GiB"


def test_soak_fails_on_growth_or_errors() -> None:
    lines: list[str] = []
    replies = iter([(200, {"objects": []}), (500, {"error": {"message": "engine failed"}})])
    code = soak.soak(
        2,
        {},
        lambda _body: next(replies),
        _health([4.0, 4.0, 9.0]),
        max_growth_gb=0.5,
        out=lines.append,
        timer=lambda: 0.0,
    )
    assert code == 1
    assert "error=engine failed" in lines[2]
    assert lines[-1] == "FAIL: 1 failed calls, allocated growth 5.00 GiB"
    assert soak.memory_line({}) == "memory=unavailable"
