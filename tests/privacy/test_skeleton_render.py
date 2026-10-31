from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pytest

from rulecam.contracts import PrivacyMode
from rulecam.privacy import (
    PersonRender,
    canonical_pose,
    ppe_from_trace,
    render_skeletons,
    track_colour,
)
from rulecam.privacy.skeleton_render import (
    FOREARM,
    HEAD_LENGTH,
    HIP_HALF_WIDTH,
    MISSING,
    PRESENT,
    SHIN,
    SHOULDER_HALF_WIDTH,
    THIGH,
    UPPER_ARM,
)
from tests.pose.helpers import NAMES, STANDING, keypoints
from tests.privacy.helpers import BACKGROUND, person, scene, stretched

ANON, STRICT = PrivacyMode.ANONYMOUS, PrivacyMode.STRICT
PLATE = scene()


def pixel(image: np.ndarray, x: int, y: int) -> tuple[int, int, int]:
    b, g, r = image[y, x]
    return int(b), int(g), int(r)


def test_standard_mode_is_not_a_skeleton_render() -> None:
    with pytest.raises(ValueError, match="anonymous and strict"):
        render_skeletons(PLATE, [person()], PrivacyMode.STANDARD)


def test_the_render_changes_only_pixels_near_the_skeleton() -> None:
    # Guardrail: the renderer draws from keypoints, so everything away from them is the plate.
    image = render_skeletons(PLATE, [person()], ANON)
    differs = (image != PLATE).any(axis=-1)
    assert differs.any()
    points = np.array([(k.x, k.y) for k in keypoints(STANDING)])
    ys, xs = np.nonzero(differs)
    nearest = np.min(np.hypot(xs[:, None] - points[:, 0], ys[:, None] - points[:, 1]), axis=1)
    assert nearest.max() <= 90  # every changed pixel lies on a bone, joint or glyph
    assert (image[:, :20] == PLATE[:, :20]).all()
    assert (image[:, 150:] == PLATE[:, 150:]).all()


def test_the_input_plate_is_not_modified() -> None:
    before = PLATE.copy()
    render_skeletons(PLATE, [person()], ANON)
    assert (before == PLATE).all()


def test_bones_use_the_hashed_track_colour_in_both_modes() -> None:
    colour = track_colour("t1")
    assert colour == track_colour("t1")
    assert colour != track_colour("t2")
    assert pixel(render_skeletons(PLATE, [person("t1")], ANON), 100, 100) == colour
    assert pixel(render_skeletons(PLATE, [person("t2")], ANON), 100, 100) == track_colour("t2")


@pytest.mark.parametrize(
    ("item", "where"),
    [("helmet", (100, 62)), ("vest", (100, 130)), ("gloves", (85, 160)), ("harness", (100, 130))],
)
def test_glyphs_are_green_when_present_and_red_when_missing(
    item: str, where: tuple[int, int]
) -> None:
    present = render_skeletons(PLATE, [person(ppe={item: True})], ANON)
    missing = render_skeletons(PLATE, [person(ppe={item: False})], ANON)
    assert pixel(present, *where) == PRESENT
    assert pixel(missing, *where) == MISSING


def test_an_unknown_item_is_outlined_not_filled() -> None:
    unknown = render_skeletons(PLATE, [person(ppe={"vest": None, "gloves": None})], ANON)
    assert pixel(unknown, 100, 130) == BACKGROUND  # the vest interior is left alone
    assert pixel(unknown, 90, 100) != BACKGROUND  # but its outline is drawn


def test_items_that_are_not_glyphs_are_ignored() -> None:
    image = render_skeletons(PLATE, [person(ppe={"boots": True})], ANON)
    assert (image == render_skeletons(PLATE, [person()], ANON)).all()


def test_glyphs_wait_for_the_joints_they_hang_on() -> None:
    hidden = keypoints(STANDING, nose=0.0, l_shoulder=0.0)
    people = [PersonRender("t1", hidden, {"helmet": True, "vest": True})]
    image = render_skeletons(PLATE, people, ANON)
    assert PRESENT not in {pixel(image, x, y) for x in range(60, 140) for y in range(50, 200)}


def bone(p: dict[int, tuple[float, float]], a: int, b: int) -> float:
    return math.dist(p[a], p[b])


def joints_of(pose: Mapping[str, tuple[float, float]]) -> dict[int, tuple[float, float]]:
    return {i: pose[name] for i, name in enumerate(NAMES)}


def test_strict_mode_gives_every_body_the_same_proportions() -> None:
    tall = canonical_pose(joints_of(stretched()))
    plain = canonical_pose(joints_of(STANDING))
    for rebuilt, original in ((tall, stretched()), (plain, STANDING)):
        neck = ((original["l_shoulder"][0] + original["r_shoulder"][0]) / 2,
                (original["l_shoulder"][1] + original["r_shoulder"][1]) / 2)  # fmt: skip
        pelvis = ((original["l_hip"][0] + original["r_hip"][0]) / 2,
                  (original["l_hip"][1] + original["r_hip"][1]) / 2)  # fmt: skip
        torso = math.dist(neck, pelvis)
        expected = {
            "nose": (neck, 0, HEAD_LENGTH),
            "l_shoulder": (neck, 5, SHOULDER_HALF_WIDTH),
            "l_hip": (pelvis, 11, HIP_HALF_WIDTH),
        }
        for _, (origin, index, factor) in expected.items():
            assert math.dist(origin, rebuilt[index]) == pytest.approx(factor * torso)
        limbs = (
            (5, 7, UPPER_ARM), (7, 9, FOREARM), (11, 13, THIGH),
            (13, 15, SHIN), (6, 8, UPPER_ARM), (12, 14, THIGH),
        )  # fmt: skip
        for upper, lower, factor in limbs:
            assert bone(rebuilt, upper, lower) == pytest.approx(factor * torso)


def test_strict_mode_keeps_the_direction_of_each_bone() -> None:
    rebuilt = canonical_pose(joints_of(stretched()))
    original = joints_of(stretched())
    for upper, lower in ((5, 7), (7, 9), (11, 13), (13, 15)):
        want = np.subtract(original[lower], original[upper])
        got = np.subtract(rebuilt[lower], rebuilt[upper])
        cosine = want @ got / (np.linalg.norm(want) * np.linalg.norm(got))
        assert cosine == pytest.approx(1.0)


def test_strict_mode_drops_the_face_points_and_needs_a_known_torso() -> None:
    rebuilt = canonical_pose(joints_of(STANDING))
    assert set(rebuilt) <= {0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16}
    assert canonical_pose({}) == {}
    assert canonical_pose({5: (0.0, 0.0), 6: (0.0, 0.0), 11: (0.0, 0.0), 12: (0.0, 0.0)}) == {}
    unknown_torso = [PersonRender("t1", keypoints(STANDING, l_hip=0.0, r_hip=0.0), {})]
    assert (render_skeletons(PLATE, unknown_torso, STRICT) == PLATE).all()


def test_strict_renders_differ_from_anonymous_ones_for_unusual_proportions() -> None:
    people = [person(pose=stretched())]
    assert (render_skeletons(PLATE, people, STRICT) != render_skeletons(PLATE, people, ANON)).any()


def test_ppe_states_come_from_the_predicate_trace() -> None:
    trace: dict[str, float | str | bool] = {
        "h": True, "v": False, "g": True, "g.unknown": True,
    }  # fmt: skip
    states = ppe_from_trace(trace, {"helmet": "h", "vest": "v", "gloves": "g", "harness": "x"})
    assert states == {"helmet": False, "vest": True, "gloves": None, "harness": None}
