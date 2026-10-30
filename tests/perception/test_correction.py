"""Correction loop: negative point, backward re-track, re-evaluation (P1-10)."""

from __future__ import annotations

import pytest

from rulecam.perception.correction import (
    CorrectionError,
    CorrectionRequest,
    UnknownEventError,
    locate_frame,
    to_point,
)
from rulecam.rules.backtest import DecisionContext
from tests.fakes import FakeSegmenter, make_chunk
from tests.perception.correction_helpers import STRAP_CENTRE, Harness, build, strap_detections
from tests.rules.helpers import CAL, at


def request(
    event_id: str, frame_idx: int = 59, xy: tuple[float, float] = STRAP_CENTRE
) -> CorrectionRequest:
    return CorrectionRequest(event_id=event_id, frame_idx=frame_idx, x=xy[0], y=xy[1])


def event_id(h: Harness) -> str:
    candidate = h.record.payload["candidate"]
    assert isinstance(candidate, dict)
    return str(candidate["event_id"])


def test_strap_disappears_after_the_negative_point_and_the_candidate_is_cancelled() -> None:
    h = build()  # the segmenter returns nothing once the strap is marked negative
    result = h.service.correct(request(event_id(h)))
    assert result.status == "cancelled"
    assert result.concept == "safety harness"
    assert result.candidate.event_id == event_id(h)
    assert result.candidate.predicate_trace["status"] == "cancelled"
    assert result.candidate.predicate_trace["cancelled_by"] == "correction"
    assert result.chunk_ids == ("cam1_000001", "cam1_000000")


def test_the_request_re_tracks_backward_from_the_click_to_the_episode_start() -> None:
    h = build()
    h.service.correct(request(event_id(h)))
    calls = h.segmenter.calls
    assert [c.chunk_id for c in calls] == ["cam1_000001", "cam1_000000"]
    assert {c.direction for c in calls} == {"backward"}
    assert {c.concept for c in calls} == {"safety harness"}
    assert calls[0].negative_points == [(29, *STRAP_CENTRE)]  # clip frame 59 = chunk 1, frame 29
    assert calls[1].negative_points == [(29, *STRAP_CENTRE)]  # strap position at chunk 0's end
    assert all(c.box_prompts is None for c in calls)


def test_the_label_is_logged() -> None:
    h = build()
    h.service.correct(request(event_id(h)))
    assert len(h.labels.records) == 1
    kind, payload = h.labels.records[0]
    assert kind == "label"
    assert payload["label"] == "false_positive"
    assert payload["reason"] == "segmentation"
    assert payload["event_id"] == event_id(h)
    assert payload["concept"] == "safety harness"
    assert payload["rule_id"] == "harness-unclipped"


def test_a_real_harness_keeps_firing() -> None:
    h = build(FakeSegmenter(responder=strap_detections))
    result = h.service.correct(request(event_id(h)))
    assert result.status == "still_firing"
    assert result.candidate.event_id == event_id(h)  # re-attached to the same subject
    assert "status" not in result.candidate.predicate_trace
    assert len(h.labels.records) == 1


def test_a_click_mid_episode_only_corrects_up_to_the_clicked_frame() -> None:
    h = build()
    result = h.service.correct(request(event_id(h), frame_idx=29))
    assert [c.chunk_id for c in h.segmenter.calls] == ["cam1_000000"]
    assert result.status == "still_firing"  # chunk 1 still shows the strap


def test_clicking_a_false_person_re_tracks_the_person() -> None:
    h = build()
    result = h.service.correct(request(event_id(h), xy=(110.0, 250.0)))
    assert result.concept == "person"
    assert result.status == "cancelled"


def test_bad_requests() -> None:
    h = build()
    with pytest.raises(UnknownEventError):
        h.service.correct(request("ev-missing"))
    with pytest.raises(CorrectionError, match="outside the event clip"):
        h.service.correct(request(event_id(h), frame_idx=90))  # the clip is 3 chunks
    with pytest.raises(CorrectionError, match="no object"):
        h.service.correct(request(event_id(h), xy=(900.0, 900.0)))
    assert h.segmenter.calls == []
    assert h.labels.records == []


def test_a_non_candidate_record_is_rejected() -> None:
    h = build()
    h.events.records[event_id(h)] = h.record.model_copy(update={"kind": "label"})
    with pytest.raises(CorrectionError, match="no usable candidate"):
        h.service.correct(request(event_id(h)))


def test_locate_frame() -> None:
    chunks = [make_chunk("cam1", 0), make_chunk("cam1", 1, n_frames=10)]
    assert locate_frame(chunks, 0) == (0, 0)
    assert locate_frame(chunks, 35) == (1, 5)
    with pytest.raises(CorrectionError):
        locate_frame(chunks, 40)


def test_new_points_use_the_box_bottom_centre_and_calibration() -> None:
    (det,) = strap_detections(make_chunk("cam1", 0, n_frames=1), "safety harness")
    context = DecisionContext(rule=build().context.rule, calibration=CAL, t0=at(0), t1=at(1))
    point = to_point(det, context)
    assert point.foot_px == (150.0, 140.0)
    assert point.foot_m == pytest.approx((1.5, 1.4))
    assert to_point(det, context.model_copy(update={"calibration": None})).foot_m is None
