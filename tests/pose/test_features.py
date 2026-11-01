from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulecam.contracts import Keypoint
from rulecam.pose.features import (
    PoseStates,
    bent_back_lift,
    hands_above_shoulder,
    is_down,
    known,
    ladder_three_point_lost,
)
from tests.pose.helpers import (
    BENDING,
    BENDING_BOX,
    LIFTING,
    LYING,
    LYING_BOX,
    SQUAT,
    STANDING,
    STANDING_BOX,
    keypoints,
    track_point,
)


def test_known_filters_by_score_and_index() -> None:
    kps = keypoints(STANDING, nose=0.29)
    assert known(kps, 0) is None
    assert known(kps, 1) == (98, 78)
    assert known(kps, 99) is None


def test_is_down_positive_and_negatives() -> None:
    assert is_down(keypoints(LYING), LYING_BOX) is True
    assert is_down(keypoints(STANDING), STANDING_BOX) is False
    assert is_down(keypoints(BENDING), BENDING_BOX) is False


def test_is_down_uses_the_one_known_side() -> None:
    kps = keypoints(LYING, r_shoulder=0.1, r_hip=0.1)
    assert is_down(kps, LYING_BOX) is True


def test_is_down_unknown_and_degenerate() -> None:
    assert is_down(keypoints(LYING, l_hip=0.1, r_hip=0.1), LYING_BOX) is None
    collapsed = {**STANDING, "l_shoulder": (92, 160), "r_shoulder": (108, 160)}
    assert is_down(keypoints(collapsed), STANDING_BOX) is None


def test_hands_above_shoulder() -> None:
    assert hands_above_shoulder(keypoints(STANDING)) is False
    raised = {**STANDING, "r_wrist": (115, 70)}
    assert hands_above_shoulder(keypoints(raised)) is True


def test_a_raised_hand_counts_even_if_the_other_side_is_unknown() -> None:
    raised = {**STANDING, "l_wrist": (85, 70)}
    assert hands_above_shoulder(keypoints(raised, r_wrist=0.1)) is True


def test_one_side_known_false_and_the_other_unknown_is_unknown_not_false() -> None:
    # The unknown wrist could still be raised, so "False" would be a guess.
    assert hands_above_shoulder(keypoints(STANDING, r_wrist=0.1)) is None
    assert hands_above_shoulder(keypoints(STANDING, l_shoulder=0.1)) is None
    assert hands_above_shoulder(keypoints(STANDING, l_wrist=0.1, r_wrist=0.1)) is None
    assert hands_above_shoulder(keypoints(STANDING)) is False


def test_ladder_three_point_lost() -> None:
    whole_body = (60.0, 60.0, 140.0, 280.0)
    legs_only = (60.0, 200.0, 140.0, 280.0)
    assert ladder_three_point_lost(keypoints(STANDING), whole_body) is False
    assert ladder_three_point_lost(keypoints(STANDING), legs_only) is True


def test_ladder_unknown_limbs_decide_only_when_they_cannot_change_the_answer() -> None:
    whole_body = (60.0, 60.0, 140.0, 280.0)
    two_wrists_hidden = keypoints(STANDING, l_wrist=0.1, r_wrist=0.1)
    assert ladder_three_point_lost(two_wrists_hidden, whole_body) is None
    far_ladder = (500.0, 500.0, 600.0, 600.0)
    assert ladder_three_point_lost(two_wrists_hidden, far_ladder) is True


def test_bent_back_lift() -> None:
    assert bent_back_lift(keypoints(LIFTING)) is True
    assert bent_back_lift(keypoints(STANDING)) is False


def test_a_correct_squat_lift_is_not_a_bent_back() -> None:
    # Hips and knees are bent and the hands are low, but the torso stays upright.
    assert bent_back_lift(keypoints(SQUAT)) is False
    assert bent_back_lift(keypoints(SQUAT), min_torso_angle_deg=0.0) is True


def test_the_torso_angle_threshold_is_adjustable() -> None:
    assert bent_back_lift(keypoints(LIFTING), min_torso_angle_deg=70.0) is False
    assert bent_back_lift(keypoints(LIFTING), min_torso_angle_deg=30.0) is True


def test_bent_back_lift_needs_hands_below_knees() -> None:
    hands_high = {**LIFTING, "l_wrist": (150, 100), "r_wrist": (158, 100)}
    assert bent_back_lift(keypoints(hands_high)) is False


def test_bent_back_lift_threshold_is_adjustable_and_unknowns_give_none() -> None:
    assert bent_back_lift(keypoints(LIFTING), max_hip_angle_deg=30.0) is False
    assert bent_back_lift(keypoints(LIFTING, l_wrist=0.1, r_wrist=0.1)) is None
    assert bent_back_lift(keypoints(LIFTING, l_knee=0.1, r_knee=0.1)) is None
    assert bent_back_lift(keypoints(LIFTING, l_shoulder=0.1, r_shoulder=0.1)) is None


def test_bent_back_lift_ignores_a_side_with_a_zero_length_limb() -> None:
    folded = {**LIFTING, "l_shoulder": (110, 160), "r_shoulder": (110, 160)}
    assert bent_back_lift(keypoints(folded, r_hip=0.1)) is None


@given(
    scores=st.lists(
        st.floats(min_value=0.0, max_value=0.29, allow_nan=False), min_size=17, max_size=17
    )
)
def test_every_feature_is_unknown_when_all_keypoints_score_low(scores: list[float]) -> None:
    kps = tuple(Keypoint(x=float(i), y=float(i), score=s) for i, s in enumerate(scores))
    assert is_down(kps, STANDING_BOX) is None
    assert hands_above_shoulder(kps) is None
    assert bent_back_lift(kps) is None
    assert ladder_three_point_lost(kps, (0.0, 0.0, 10.0, 10.0)) is None


def test_adapter_dispatches_each_state_and_handles_missing_keypoints() -> None:
    legs_only = (60.0, 200.0, 140.0, 280.0)
    states = PoseStates(ladder_region=lambda _point: legs_only)
    standing = track_point(box=STANDING_BOX).model_copy(update={"keypoints": keypoints(STANDING)})
    lying = track_point(box=LYING_BOX).model_copy(update={"keypoints": keypoints(LYING)})
    lifting = track_point().model_copy(update={"keypoints": keypoints(LIFTING)})
    assert states("down", lying) is True
    assert states("down", standing) is False
    assert states("hands_above_shoulder", standing) is False
    assert states("bent_back_lift", lifting) is True
    assert states("ladder_three_point_lost", standing) is True
    assert states("down", track_point()) is None


@pytest.mark.parametrize("region", [None, lambda _point: None])
def test_adapter_ladder_state_is_unknown_without_a_region(region: object) -> None:
    states = PoseStates(ladder_region=region)  # type: ignore[arg-type]
    point = track_point().model_copy(update={"keypoints": keypoints(STANDING)})
    assert states("ladder_three_point_lost", point) is None
