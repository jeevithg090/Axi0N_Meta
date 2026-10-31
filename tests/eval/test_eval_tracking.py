"""Tracking metrics: MOT parsing, HOTA, IDF1 / ID switches, and scoring a run with tracks."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from tests.fakes import EPOCH
from tests.fakes.eval_api import run_record

from rulecam.eval.errors import EvalError
from rulecam.eval.record import PredictedBox
from rulecam.eval.schema import load_eval_set
from rulecam.eval.score import score_runs
from rulecam.eval.tracking import (
    FrameBoxes,
    clear_and_id,
    hota,
    iou_matrix,
    parse_mot,
    predicted_frames,
    score_tracking,
)

A = [(0.0, 0.0, 10.0, 10.0), (1.0, 0.0, 11.0, 10.0), (2.0, 0.0, 12.0, 10.0)]
B = [(20.0, 20.0, 30.0, 30.0), (20.0, 21.0, 30.0, 31.0), (20.0, 22.0, 30.0, 32.0)]


def two_objects(ids: list[tuple[str, str]]) -> dict[int, FrameBoxes]:
    return {f + 1: FrameBoxes(list(ids[f]), [A[f], B[f]]) for f in range(3)}


GT = two_objects([("a", "b")] * 3)


def test_iou_matrix() -> None:
    assert iou_matrix(
        [(0, 0, 10, 10)], [(0, 0, 10, 10), (5, 0, 15, 10), (50, 50, 60, 60)]
    ).tolist() == [[1.0, pytest.approx(1 / 3), 0.0]]
    assert iou_matrix([], [(0, 0, 1, 1)]).shape == (0, 1)


def test_perfect_tracking() -> None:
    assert hota(GT, GT, [1, 2, 3]) == pytest.approx(1.0)
    assert clear_and_id(GT, GT, [1, 2, 3]) == (1.0, 0, 1.0)


def test_an_identity_swap_lowers_hota_and_idf1() -> None:
    swapped = two_objects([("x", "y"), ("x", "y"), ("y", "x")])
    idf1, switches, mota = clear_and_id(GT, swapped, [1, 2, 3])
    assert switches == 2
    assert idf1 == pytest.approx(2 / 3)
    assert mota == pytest.approx(2 / 3)
    # AssA = (2 * 1/2 + 1 * 1/5) * 2 / 6 = 0.4, DetA = 1: HOTA = sqrt(0.4).
    assert hota(GT, swapped, [1, 2, 3]) == pytest.approx(0.4**0.5)


def test_nothing_tracked() -> None:
    assert hota(GT, {}, [1, 2, 3]) == 0.0
    assert clear_and_id(GT, {}, [1, 2, 3])[0] == 0.0
    sparse = {1: FrameBoxes(["x"], [A[0]])}
    assert 0 < hota(GT, sparse, [1, 2, 3]) < 1


def test_parse_mot() -> None:
    lines = [
        "1,7,0,0,10,10,1,1,1",
        "1,8,20,20,10,10,1,2,1",
        "2,7,1,0,10,10,1,9,1",  # unknown class: dropped
        "",
    ]
    parsed = parse_mot(lines, {1: "person", 2: "forklift"})
    assert parsed["person"][1].ids == ["7"]
    assert parsed["person"][1].boxes == [(0, 0, 10, 10)]
    assert parsed["forklift"][1].boxes == [(20, 20, 30, 30)]
    no_class = parse_mot(["3,1,0,0,5,5"], {1: "person"})
    assert no_class["person"][3].ids == ["1"]
    with pytest.raises(EvalError, match="fewer"):
        parse_mot(["1,2,3"], {1: "person"})
    with pytest.raises(EvalError, match="malformed"):
        parse_mot(["1,x,0,0,1,1"], {1: "person"})


def test_predicted_frames_scale_and_round() -> None:
    boxes = [PredictedBox(clip_s=0.04, track="t", concept="person", box_xyxy=(1, 2, 3, 4))]
    frames, scored = predicted_frames(boxes, fps=25, scale=(2.0, 3.0))
    assert scored == {2}
    assert frames["person"][2].boxes == [(2, 6, 6, 12)]


def test_score_tracking_per_concept() -> None:
    gt = {"person": GT}
    predicted = [
        PredictedBox(clip_s=f / 10, track=tid, concept="person", box_xyxy=box)
        for f in range(3)
        for tid, box in (("p1", A[f]), ("p2", B[f]))
    ]
    (scores,) = score_tracking(gt, predicted, fps=10)
    assert scores.frames == 3
    assert scores.gt_ids == 2
    assert scores.hota == pytest.approx(1.0)
    assert scores.id_switches_per_min == 0
    assert score_tracking(gt, [], fps=10)[0].id_switches_per_min is None


def test_a_run_with_labelled_tracks_is_scored(tmp_path: Path) -> None:
    folder = tmp_path / "yard"
    folder.mkdir()
    rows = [f"{f + 1},1,{A[f][0]},{A[f][1]},10,10,1,1,1" for f in range(3)]
    (folder / "gt.txt").write_text("\n".join(rows), encoding="utf-8")
    clip = {
        "name": "yard",
        "camera_id": "yard",
        "source": {"path": "clip.mp4"},
        "duration_s": 30,
        "rules": [{"id": "forklift_2m", "request": "x", "status": "negative"}],
        "tracks": {
            "file": "gt.txt",
            "fps": 10,
            "classes": {1: "person"},
            "width": 1280,
            "height": 720,
        },
    }
    (folder / "clip.yaml").write_text(yaml.safe_dump(clip), encoding="utf-8")
    predicted = tuple(
        PredictedBox(
            clip_s=f / 10, track="p", concept="person", box_xyxy=tuple(v / 2 for v in A[f])
        )
        for f in range(3)
    )
    run = run_record([], clip="yard", predicted=predicted, chunk_width=640, chunk_height=360)
    report = score_runs([run], load_eval_set(tmp_path), generated_at=EPOCH + timedelta(0))
    (row,) = report.runs[0].tracking
    assert row.concept == "person"
    assert row.hota == pytest.approx(1.0)
    assert row.id_switches == 0
