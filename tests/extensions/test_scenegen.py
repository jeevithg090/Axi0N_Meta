from __future__ import annotations

import pytest
from pydantic import ValidationError

from rulecam.extensions.scenegen import (
    CheckSpec,
    SampleJSON,
    boundary_scenes,
    copy_sample,
    foot_point,
    generate,
    inside_frame,
    line_axis,
    line_coord_at,
    make_object,
    make_sample,
    point_in_polygon,
    shrink,
    side_of_line,
    simpler,
)
from tests.extensions.checks import DOCK, SPEC

SAMPLE_KEYS = {"timestamp", "frame_w", "frame_h", "objects", "zones", "lines", "px_per_m"}
OBJECT_KEYS = {"concept", "box_xyxy_px", "mask_top_y_px", "mask_area_px", "ground_xy_m", "score"}


def _singles(samples: list[SampleJSON], concept: str = "pallet") -> list[list[float]]:
    return [
        s["objects"][0]["box_xyxy_px"]
        for s in samples
        if len(s["objects"]) == 1 and s["objects"][0]["concept"] == concept
    ]


# --- spec and geometry ----------------------------------------------------------------------


def test_spec_rejects_inconsistent_input() -> None:
    with pytest.raises(ValidationError, match="unrelated"):
        CheckSpec(concepts=("pallet",), unrelated_concepts=("pallet",))
    with pytest.raises(ValidationError, match="at least 3 points"):
        CheckSpec(concepts=("pallet",), zones={"bad": ((0.0, 0.0), (1.0, 1.0))})
    with pytest.raises(ValidationError, match="zero length"):
        CheckSpec(concepts=("pallet",), lines={"dot": ((5.0, 5.0), (5.0, 5.0))})
    with pytest.raises(ValidationError):
        CheckSpec(concepts=())


def test_line_geometry() -> None:
    flat = [[0.0, 100.0], [200.0, 200.0]]
    assert line_axis(flat) == "horizontal"
    assert line_coord_at(flat, 100.0) == pytest.approx(150.0)
    assert line_coord_at(flat, 400.0) == pytest.approx(300.0)  # extended past its end
    upright = [[50.0, 0.0], [70.0, 200.0]]
    assert line_axis(upright) == "vertical"
    assert line_coord_at(upright, 100.0) == pytest.approx(60.0)
    assert side_of_line([0.0, 0.0, 20.0, 90.0], flat) == "above"
    assert side_of_line([0.0, 120.0, 20.0, 160.0], flat) == "below"
    assert side_of_line([0.0, 90.0, 20.0, 160.0], flat) == "straddles"
    assert side_of_line([0.0, 90.0, 40.0, 110.0], upright) == "left"
    assert side_of_line([80.0, 90.0, 99.0, 110.0], upright) == "right"
    assert side_of_line([50.0, 90.0, 80.0, 110.0], upright) == "straddles"


def test_point_in_polygon_and_foot_point() -> None:
    assert point_in_polygon((300.0, 500.0), DOCK)
    assert not point_in_polygon((100.0, 500.0), DOCK)
    assert not point_in_polygon((300.0, 390.0), DOCK)
    assert foot_point([10.0, 20.0, 30.0, 60.0]) == (20.0, 60.0)


def test_make_object_keeps_mask_and_ground_consistent() -> None:
    obj = make_object("pallet", (100.0, 200.0, 180.0, 260.0), px_per_m=100.0, mask_fill=0.5)
    assert obj["mask_top_y_px"] == 200.0
    assert obj["mask_area_px"] == pytest.approx(80 * 60 * 0.5)
    assert obj["ground_xy_m"] == pytest.approx([1.4, 2.6])
    assert make_object("pallet", (0.0, 0.0, 1.0, 1.0), px_per_m=None)["ground_xy_m"] is None


# --- generate -------------------------------------------------------------------------------


def test_generate_is_deterministic_and_seeded() -> None:
    first = generate(SPEC, n_random=30, seed=1)
    assert first == generate(SPEC, n_random=30, seed=1)
    assert first != generate(SPEC, n_random=30, seed=2)
    boundary = boundary_scenes(SPEC)
    assert len(generate(SPEC, n_random=0)) == len(boundary)
    assert len(first) == len(boundary) + 30
    with pytest.raises(ValueError, match="n_random"):
        generate(SPEC, n_random=-1)


def test_every_sample_has_the_check_format_and_fits_the_frame() -> None:
    samples = generate(SPEC, n_random=60, seed=3)
    stamps = [s["timestamp"] for s in samples]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)
    for sample in samples:
        assert set(sample) == SAMPLE_KEYS
        assert inside_frame(sample)
        for obj in sample["objects"]:
            assert set(obj) == OBJECT_KEYS
            x1, y1, x2, y2 = obj["box_xyxy_px"]
            assert x1 < x2
            assert y1 < y2
            assert obj["mask_area_px"] >= 0
            ppm = sample["px_per_m"]
            if ppm is None:
                assert obj["ground_xy_m"] is None
            else:
                fx, fy = foot_point(obj["box_xyxy_px"])
                assert obj["ground_xy_m"] == pytest.approx([fx / ppm, fy / ppm])


def test_boundary_scenes_put_edges_either_side_of_the_line() -> None:
    boxes = _singles(boundary_scenes(SPEC))
    tops = {box[1] for box in boxes}
    bottoms = {box[3] for box in boxes}
    edges = {300.0 + d for d in (-8.0, -3.0, -1.0, 1.0, 3.0, 8.0)}
    assert edges <= tops
    assert edges <= bottoms
    line = [list(p) for p in SPEC.lines["yellow"]]
    sides = {side_of_line(box, line) for box in boxes}
    assert {"above", "below", "straddles"} <= sides


def test_boundary_scenes_put_feet_either_side_of_each_zone_edge() -> None:
    feet = [foot_point(box) for box in _singles(boundary_scenes(SPEC))]
    inside = [f for f in feet if point_in_polygon(f, DOCK)]
    outside = [f for f in feet if not point_in_polygon(f, DOCK)]
    assert any(abs(fy - 401.0) < 1e-9 for _, fy in inside)  # 1 px inside the top edge
    assert any(abs(fy - 399.0) < 1e-9 for _, fy in outside)  # 1 px outside it


def test_boundary_scenes_cover_counts_missing_shapes_and_odd_values() -> None:
    scenes = boundary_scenes(SPEC)
    counts = {len(s["objects"]) for s in scenes}
    assert {0, 1, 2, 8} <= counts
    assert any(s["lines"] == {} for s in scenes)
    assert any(s["zones"] == {} for s in scenes)
    objects = [obj for s in scenes for obj in s["objects"]]
    assert any(obj["mask_area_px"] == 0 for obj in objects)
    assert any(obj["score"] is None for obj in objects)
    assert any(obj["score"] == pytest.approx(0.1) for obj in objects)
    assert any(obj["concept"] == "traffic cone" for obj in objects)
    assert any(s["px_per_m"] is None for s in scenes)
    boxes = [obj["box_xyxy_px"] for obj in objects]
    assert any(b[0] == 0 for b in boxes)
    assert any(b[1] == 0 for b in boxes)
    assert any(b[2] == SPEC.frame_w for b in boxes)
    assert any(b[3] == SPEC.frame_h for b in boxes)


def test_upright_lines_get_left_and_right_edge_scenes() -> None:
    spec = CheckSpec(concepts=("pallet",), lines={"post": ((640.0, 50.0), (640.0, 670.0))})
    boxes = _singles(boundary_scenes(spec))
    lefts = {box[0] for box in boxes}
    assert {639.0, 641.0, 632.0, 648.0} <= lefts
    line = [list(p) for p in spec.lines["post"]]
    assert {"left", "right", "straddles"} <= {side_of_line(b, line) for b in boxes}
    assert all(s["px_per_m"] is None for s in boundary_scenes(spec))


def test_scenes_that_would_leave_the_frame_are_skipped() -> None:
    spec = CheckSpec(concepts=("pallet",), lines={"top": ((100.0, 2.0), (1180.0, 2.0))})
    assert all(inside_frame(s) for s in generate(spec, n_random=10))


def test_random_scenes_mix_concepts() -> None:
    random_part = generate(SPEC, n_random=80, seed=5)[len(boundary_scenes(SPEC)) :]
    concepts = {obj["concept"] for s in random_part for obj in s["objects"]}
    assert concepts == {"pallet", "traffic cone"}


def test_inside_frame_handles_unknown_frames() -> None:
    sample = make_sample(SPEC, [make_object("pallet", (1200.0, 0.0, 1300.0, 50.0), px_per_m=None)])
    assert not inside_frame(sample)
    sample["frame_w"] = None
    assert inside_frame(sample)


# --- shrink ---------------------------------------------------------------------------------


def _above_yellow(sample: SampleJSON) -> bool:
    line = sample["lines"].get("yellow")
    if line is None:
        return False
    return any(
        obj["concept"] == "pallet" and side_of_line(obj["box_xyxy_px"], line) == "above"
        for obj in sample["objects"]
    )


def _busy_sample() -> SampleJSON:
    objects = [
        make_object("pallet", (500.4, 400.2, 580.6, 460.9), px_per_m=100.0),
        make_object("traffic cone", (40.0, 40.0, 80.0, 90.0), px_per_m=100.0),
        make_object("pallet", (200.4, 200.4, 280.6, 260.6), px_per_m=100.0),
        make_object("pallet", (900.0, 500.0, 980.0, 560.0), px_per_m=100.0),
    ]
    return make_sample(SPEC, objects)


def test_shrink_reduces_to_one_whole_pixel_object_next_to_the_line() -> None:
    calls: list[int] = []

    def fails(candidates: list[SampleJSON]) -> list[bool]:
        calls.append(len(candidates))
        return [_above_yellow(c) for c in candidates]

    busy = _busy_sample()
    before = copy_sample(busy)
    small = shrink(busy, fails)
    assert busy == before  # the input is never changed
    assert _above_yellow(small)
    assert len(small["objects"]) == 1
    assert small["zones"] == {}
    assert "yellow" in small["lines"]
    assert small["px_per_m"] is None
    obj = small["objects"][0]
    assert all(float(v).is_integer() for v in obj["box_xyxy_px"])
    assert 0 < 300.0 - obj["box_xyxy_px"][3] < 2  # moved right up to the line
    assert all(n > 1 for n in calls)  # candidates are judged in batches


def test_shrink_keeps_the_sample_when_nothing_simpler_fails() -> None:
    busy = _busy_sample()
    assert shrink(busy, lambda cands: [False] * len(cands)) == busy
    assert shrink(busy, lambda cands: [True] * len(cands), max_rounds=0) == busy


def test_simpler_offers_drops_rounding_and_moves() -> None:
    busy = _busy_sample()
    candidates = simpler(busy)
    assert any(len(c["objects"]) == 3 for c in candidates)
    assert any(c["zones"] == {} for c in candidates)
    assert any(c["lines"] == {} for c in candidates)
    assert any(c["px_per_m"] is None for c in candidates)
    rounded = [
        c for c in candidates if all(float(v).is_integer() for v in c["objects"][0]["box_xyxy_px"])
    ]
    assert rounded
    assert simpler(make_sample(CheckSpec(concepts=("pallet",)), [])) == []
