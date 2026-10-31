from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pytest
from numpy.typing import NDArray

from rulecam.contracts import Keypoint, Track
from rulecam.pose.keypoints import (
    INPUT_H,
    INPUT_W,
    SIMCC_SPLIT_RATIO,
    CropWindow,
    RtmposeOnnx,
    crop_window,
    decode_simcc,
    estimate_track,
    fill_keypoints,
    prepare_input,
    sample_indices,
    to_image_keypoints,
)
from tests.pose.helpers import track_point

K = 17
SIMCC_W, SIMCC_H = int(INPUT_W * SIMCC_SPLIT_RATIO), int(INPUT_H * SIMCC_SPLIT_RATIO)


def simcc(
    peak_x: int, peak_y: int, height: float = 0.8
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    sx = np.zeros((1, K, SIMCC_W), dtype=np.float32)
    sy = np.zeros((1, K, SIMCC_H), dtype=np.float32)
    sx[0, :, peak_x] = height
    sy[0, :, peak_y] = height
    return sx, sy


class FakeSession:
    def __init__(self, outputs: tuple[NDArray[np.float32], NDArray[np.float32]]) -> None:
        self.outputs = outputs
        self.feeds: list[Mapping[str, NDArray[np.float32]]] = []

    def run(
        self, output_names: object, input_feed: Mapping[str, NDArray[np.float32]]
    ) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
        self.feeds.append(input_feed)
        return self.outputs


def test_crop_window_has_model_aspect_and_contains_the_box() -> None:
    window = crop_window((70.0, 80.0, 130.0, 265.0))
    assert window.width / window.height == pytest.approx(INPUT_W / INPUT_H)
    assert window.x0 + window.width / 2 == pytest.approx(100.0)
    assert window.y0 + window.height / 2 == pytest.approx(172.5)
    assert window.x0 < 70
    assert window.x0 + window.width > 130
    assert window.y0 < 80
    assert window.y0 + window.height > 265


def test_crop_window_grows_height_for_a_wide_box() -> None:
    window = crop_window((0.0, 0.0, 300.0, 50.0))
    assert window.width / window.height == pytest.approx(INPUT_W / INPUT_H)
    assert window.height > 50


def test_prepare_input_shape_dtype_and_normalisation() -> None:
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    tensor = prepare_input(frame, CropWindow(50.0, 40.0, 192.0, 256.0))
    assert tensor.shape == (1, 3, INPUT_H, INPUT_W)
    assert tensor.dtype == np.float32
    assert tensor[0, 0, 0, 0] == pytest.approx(-123.675 / 58.395, rel=1e-4)
    assert tensor[0, 2, 0, 0] == pytest.approx(-103.53 / 57.375, rel=1e-4)


def test_prepare_input_pads_outside_the_frame_with_black() -> None:
    frame = np.full((100, 100, 3), 255, dtype=np.uint8)
    tensor = prepare_input(frame, CropWindow(-96.0, -128.0, 192.0, 256.0))
    assert tensor[0, 0, 0, 0] == pytest.approx(-123.675 / 58.395, rel=1e-4)
    assert tensor[0, 0, 178, 146] > 0  # image pixel (50, 50) is inside the frame


def test_decode_simcc_positions_and_scores() -> None:
    sx, sy = simcc(peak_x=100, peak_y=200)
    sy[0, 3, 200] = 0.5
    positions, scores = decode_simcc(sx, sy)
    assert positions.shape == (K, 2)
    assert tuple(positions[0]) == (50.0, 100.0)
    assert scores[0] == pytest.approx(0.8)
    assert scores[3] == pytest.approx(0.5)


def test_to_image_keypoints_maps_the_input_centre_to_the_window_centre() -> None:
    window = CropWindow(100.0, 50.0, 96.0, 128.0)
    positions = np.array([[INPUT_W / 2, INPUT_H / 2]], dtype=np.float32)
    (keypoint,) = to_image_keypoints(positions, np.array([0.7], dtype=np.float32), window)
    assert (keypoint.x, keypoint.y) == pytest.approx((148.0, 114.0))
    assert keypoint.score == pytest.approx(0.7)


def test_rtmpose_estimates_keypoints_inside_the_box() -> None:
    box = (100.0, 50.0, 160.0, 230.0)
    session = FakeSession(simcc(peak_x=INPUT_W, peak_y=INPUT_H))
    estimator = RtmposeOnnx(session, input_name="input")
    keypoints = estimator.estimate(np.zeros((300, 400, 3), dtype=np.uint8), box)
    assert len(keypoints) == K
    assert (keypoints[0].x, keypoints[0].y) == pytest.approx((130.0, 140.0), abs=1.0)
    assert keypoints[0].score == pytest.approx(0.8)
    assert list(session.feeds[0]) == ["input"]


@pytest.mark.parametrize(
    ("count", "stride", "expected"),
    [(10, 3, (0, 3, 6, 9)), (10, 4, (0, 4, 8, 9)), (1, 5, (0,)), (0, 3, ())],
)
def test_sample_indices(count: int, stride: int, expected: tuple[int, ...]) -> None:
    assert sample_indices(count, stride) == expected


def test_sample_indices_rejects_a_zero_stride() -> None:
    with pytest.raises(ValueError, match="stride"):
        sample_indices(5, 0)


def kps(x: float, score: float = 0.9) -> tuple[Keypoint, ...]:
    return tuple(Keypoint(x=x, y=2 * x, score=score) for _ in range(K))


def test_fill_keypoints_interpolates_between_estimates() -> None:
    points = [track_point(i) for i in range(5)]
    filled = fill_keypoints(points, {0: kps(0.0), 4: kps(40.0, score=0.5)})
    assert filled[0].keypoints == kps(0.0)
    assert filled[4].keypoints == kps(40.0, score=0.5)
    middle = filled[2].keypoints
    assert middle is not None
    assert (middle[0].x, middle[0].y) == pytest.approx((20.0, 40.0))
    assert middle[0].score == 0.5


def test_fill_keypoints_leaves_edges_and_wide_gaps_empty() -> None:
    points = [track_point(i) for i in range(10)]
    filled = fill_keypoints(points, {2: kps(2.0), 3: kps(3.0), 9: kps(9.0)}, max_gap_frames=4)
    assert filled[0].keypoints is None  # before the first estimate
    assert filled[5].keypoints is None  # gap of 6 frames is wider than 4
    assert filled[3].keypoints == kps(3.0)
    assert fill_keypoints(points, {}) == tuple(points)


def test_fill_keypoints_weights_by_frame_index_not_list_position() -> None:
    points = [track_point(0), track_point(2), track_point(8)]
    filled = fill_keypoints(points, {0: kps(0.0), 2: kps(80.0)}, max_gap_frames=20)
    middle = filled[1].keypoints
    assert middle is not None
    assert middle[0].x == pytest.approx(20.0)  # frame 2 of 0..8 is a quarter of the way
    assert (
        fill_keypoints(points, {0: kps(0.0), 2: kps(80.0)}, max_gap_frames=7)[1].keypoints is None
    )


def test_fill_keypoints_rejects_mismatched_keypoint_counts() -> None:
    points = [track_point(i) for i in range(3)]
    with pytest.raises(ValueError, match="same keypoint count"):
        fill_keypoints(points, {0: kps(0.0), 2: kps(2.0)[:5]})


class CountingEstimator:
    def __init__(self) -> None:
        self.calls: list[tuple[float, ...]] = []

    def estimate(self, frame: object, box_xyxy: tuple[float, ...]) -> tuple[Keypoint, ...]:
        self.calls.append(box_xyxy)
        return kps(float(len(self.calls)))


def test_estimate_track_samples_then_fills_and_keeps_the_input_unchanged() -> None:
    track = Track(
        track_id="t1",
        camera_id="cam1",
        concept="person",
        points=tuple(track_point(i) for i in range(7)),
    )
    estimator = CountingEstimator()
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = estimate_track(track, lambda _idx: frame, estimator, stride=3)
    assert len(estimator.calls) == 3  # indices 0, 3, 6
    assert all(p.keypoints is not None for p in result.points)
    assert all(p.keypoints is None for p in track.points)


def test_estimate_track_skips_frames_that_are_gone() -> None:
    track = Track(
        track_id="t1",
        camera_id="cam1",
        concept="person",
        points=tuple(track_point(i) for i in range(4)),
    )
    estimator = CountingEstimator()
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = estimate_track(track, lambda idx: frame if idx == 0 else None, estimator, stride=3)
    assert len(estimator.calls) == 1
    assert result.points[0].keypoints is not None
    assert result.points[3].keypoints is None
