from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pytest

from rulecam.contracts.core import PointXY
from rulecam.runtime.skeleton import (
    FOREARM,
    GLYPH_COLOUR,
    HEAD_LENGTH,
    HEAD_RADIUS,
    HIP_HALF_WIDTH,
    SHIN,
    SHOULDER_HALF_WIDTH,
    THIGH,
    UPPER_ARM,
    Anchors,
    PpeMark,
    Region,
    body_joints,
    canonical_head,
    canonical_pose,
    draw_ppe,
)
from rulecam.runtime.strip_draw import (
    KEYPOINT_MIN_SCORE,
    STRICT_COLOUR,
    Frame,
    Item,
    draw_person,
    person_figure,
)
from tests.pose.helpers import NAMES, STANDING, STANDING_BOX

BACKGROUND = (60, 60, 60)
FACE = range(5)


def blank() -> Frame:
    return np.full((300, 200, 3), BACKGROUND, dtype=np.uint8)


def joints_of(pose: Mapping[str, tuple[float, float]]) -> dict[int, PointXY]:
    return body_joints([(*pose[name], 0.9) for name in NAMES], KEYPOINT_MIN_SCORE)


def stretched() -> dict[str, tuple[float, float]]:
    """STANDING with longer limbs: the same torso, different proportions."""
    out: dict[str, tuple[float, float]] = dict(STANDING)
    for name, (x, y) in STANDING.items():
        if name.endswith(("_elbow", "_wrist", "_knee", "_ankle")):
            out[name] = (100 + (x - 100) * 1.4, 160 + (y - 160) * 1.4)
    return out


def person(
    key: str = "cam1:person:1",
    pose: Mapping[str, tuple[float, float]] = STANDING,
    ppe: tuple[PpeMark, ...] = (),
) -> Item:
    return Item(
        concept="person",
        box=STANDING_BOX,
        person=True,
        colour_key=key,
        keypoints=tuple((*pose[name], 0.9) for name in NAMES),
        ppe=ppe,
    )


def neck_pelvis(pose: Mapping[str, tuple[float, float]]) -> tuple[PointXY, PointXY, float]:
    neck = np.mean([pose["l_shoulder"], pose["r_shoulder"]], axis=0)
    pelvis = np.mean([pose["l_hip"], pose["r_hip"]], axis=0)
    return (
        (float(neck[0]), float(neck[1])),
        (float(pelvis[0]), float(pelvis[1])),
        float(np.linalg.norm(neck - pelvis)),
    )


# --- strict-mode body normalisation ----------------------------------------------------------


@pytest.mark.parametrize("pose", [STANDING, stretched()])
def test_canonical_pose_gives_every_body_the_same_proportions(
    pose: Mapping[str, tuple[float, float]],
) -> None:
    rebuilt = canonical_pose(joints_of(pose))
    neck, pelvis, torso = neck_pelvis(pose)
    assert math.dist(neck, rebuilt[5]) == pytest.approx(SHOULDER_HALF_WIDTH * torso)
    assert math.dist(pelvis, rebuilt[11]) == pytest.approx(HIP_HALF_WIDTH * torso)
    limbs = (
        (5, 7, UPPER_ARM), (7, 9, FOREARM), (6, 8, UPPER_ARM), (8, 10, FOREARM),
        (11, 13, THIGH), (13, 15, SHIN), (12, 14, THIGH), (14, 16, SHIN),
    )  # fmt: skip
    for upper, lower, factor in limbs:
        assert math.dist(rebuilt[upper], rebuilt[lower]) == pytest.approx(factor * torso)


def test_canonical_pose_keeps_the_direction_of_each_bone() -> None:
    original = joints_of(stretched())
    rebuilt = canonical_pose(original)
    for upper, lower in ((5, 7), (7, 9), (11, 13), (13, 15)):
        want = np.subtract(original[lower], original[upper])
        got = np.subtract(rebuilt[lower], rebuilt[upper])
        assert want @ got / (np.linalg.norm(want) * np.linalg.norm(got)) == pytest.approx(1.0)


def test_canonical_pose_needs_a_torso_and_never_rebuilds_the_face() -> None:
    joints = joints_of(STANDING)
    assert not set(joints) & set(FACE)  # body_joints already drops nose, eyes and ears
    assert set(canonical_pose(joints)) == set(range(5, 17))
    assert canonical_pose({}) == {}
    assert canonical_pose(dict.fromkeys((5, 6, 11, 12), (5.0, 5.0))) == {}  # zero-length torso
    no_left_elbow = {k: p for k, p in joints.items() if k != 7}
    assert {7, 9} & set(canonical_pose(no_left_elbow)) == set()  # the chain stops there
    folded = {**joints, 7: joints[5]}  # an elbow on its shoulder has no direction
    assert {7, 9} & set(canonical_pose(folded)) == set()
    assert canonical_head({}) is None


def test_canonical_head_sits_above_the_neck_at_a_fixed_size() -> None:
    head = canonical_head(joints_of(STANDING))
    assert head is not None
    neck, _, torso = neck_pelvis(STANDING)
    (cx, cy), radius = head
    assert (cx, cy) == pytest.approx((neck[0], neck[1] - HEAD_LENGTH * torso))
    assert radius == pytest.approx(HEAD_RADIUS * torso)


def test_strict_figures_hide_proportions_that_anonymous_ones_show() -> None:
    plain, tall = person(), person(pose=stretched())
    strict = [person_figure(p, strict=True).bones for p in (plain, tall)]
    lengths = [sorted(round(math.dist(a, b), 6) for a, b in bones) for bones in strict]
    assert lengths[0] == lengths[1]
    anonymous = [person_figure(p, strict=False).bones for p in (plain, tall)]
    assert anonymous[0] != anonymous[1]


def test_strict_mode_without_a_torso_falls_back_to_the_stick_figure() -> None:
    item = person()
    blind = Item(
        concept="person",
        box=item.box,
        person=True,
        keypoints=tuple(
            (x, y, 0.9 if k < 11 else 0.0) for k, (x, y, _) in enumerate(item.keypoints or ())
        ),
    )
    assert len(person_figure(blind, strict=True).bones) == 6  # the canonical stick figure


# --- guardrails: no face keypoints, one neutral colour in strict mode ------------------------


@pytest.mark.parametrize("strict", [False, True])
def test_face_keypoints_are_never_part_of_a_figure(strict: bool) -> None:
    item = person()
    figure = person_figure(item, strict=strict)
    ends = {point for bone in figure.bones for point in bone}
    face = {(x, y) for x, y, _ in (item.keypoints or ())[:5]}
    assert ends
    assert not ends & face


def test_strict_mode_draws_every_person_in_one_neutral_colour() -> None:
    renders = []
    for key in ("cam1:person:1", "cam1:person:2", "cam1:person:3"):
        frame = blank()
        draw_person(frame, person(key), strict=True)
        renders.append(frame)
    assert all(np.array_equal(renders[0], other) for other in renders[1:])
    drawn = renders[0][(renders[0] != BACKGROUND).any(axis=2)]
    colours, counts = np.unique(drawn, axis=0, return_counts=True)
    assert tuple(colours[counts.argmax()]) == STRICT_COLOUR


def test_anonymous_mode_still_tells_people_apart_but_marks_stay_neutral() -> None:
    marks = (PpeMark(region="full", seen=True),)
    one, two = blank(), blank()
    draw_person(one, person("cam1:person:1", ppe=marks), strict=False)
    draw_person(two, person("cam1:person:2", ppe=marks), strict=False)
    assert not np.array_equal(one, two)
    vest = (100, 140)  # inside the torso, away from every bone
    assert tuple(one[vest[1], vest[0]]) == GLYPH_COLOUR == tuple(two[vest[1], vest[0]])


# --- PPE marks -------------------------------------------------------------------------------

ANCHORS = Anchors(
    head=((100.0, 60.0), 12.0),
    torso=((80.0, 100.0), (120.0, 100.0), (120.0, 160.0), (80.0, 160.0)),
    wrists=((60.0, 200.0), (140.0, 200.0)),
)


@pytest.mark.parametrize(
    ("region", "inside", "centre"),
    [
        ("full", (90, 110), (100, 130)),
        ("top_25pct", (92, 48), (100, 46)),
    ],
)
def test_a_seen_item_is_filled_and_a_missing_one_crossed(
    region: Region, inside: tuple[int, int], centre: tuple[int, int]
) -> None:
    seen, missing = blank(), blank()
    draw_ppe(seen, ANCHORS, [PpeMark(region=region, seen=True)], 2)
    draw_ppe(missing, ANCHORS, [PpeMark(region=region, seen=False)], 2)
    x, y = inside  # inside the mark, off its diagonals
    assert tuple(seen[y, x]) == GLYPH_COLOUR
    assert tuple(missing[y, x]) == BACKGROUND
    cx, cy = centre  # the diagonals cross here
    assert tuple(missing[cy, cx]) == GLYPH_COLOUR


def test_hand_marks_ring_every_known_wrist() -> None:
    frame = blank()
    draw_ppe(frame, ANCHORS, [PpeMark(region="hands", seen=True)], 2)
    assert tuple(frame[200, 60]) == GLYPH_COLOUR
    assert tuple(frame[200, 140]) == GLYPH_COLOUR


def test_a_mark_without_its_anchor_is_skipped() -> None:
    frame = blank()
    nothing = Anchors(head=None, torso=(), wrists=())
    regions: tuple[Region, ...] = ("full", "top_25pct", "hands")
    marks = [PpeMark(region=r, seen=True) for r in regions]
    draw_ppe(frame, nothing, marks, 2)
    assert (frame == BACKGROUND).all()
