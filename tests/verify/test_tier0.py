"""Tier 0 (add-on 3.3): crop-zoom findings on the rule's PPE regions, as context for Glimmer."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from rulecam.contracts import CandidateEvent, Keypoint, Track, TrackPoint
from rulecam.contracts.core import BoxXYXY
from rulecam.perception.crop_zoom import CropHit
from rulecam.verify.tier0 import CropZoomCheck, Frame, subject_tracks, zoom_items
from rulecam.verify.tier1 import GlimmerVerifier, build_prompt
from tests.fakes import EPOCH, InMemoryTrackStore
from tests.rules.helpers import make_rule
from tests.verify.helpers import chat_reply, load_rule, make_candidate, make_strip
from tests.verify.test_tier1 import FIXTURE_ANSWER, QueueTransport, make_verifier

PERSON: BoxXYXY = (200.0, 100.0, 260.0, 300.0)
PPE = make_rule(
    {
        "op": "any",
        "of": [
            {"op": "without", "subject": "person", "item": "hair_net", "region": "top_25pct"},
            {"op": "without", "subject": "person", "item": "glove", "region": "hands"},
            {"op": "without", "subject": "person", "item": "glove", "region": "hands"},
        ],
    },
    concepts={"person": "person", "hair_net": "hair net", "glove": "disposable glove"},
    rule_id="kitchen-ppe",
)


def person_track(track_id: str = "cam1:person:1", height: float = 200.0) -> Track:
    box = (PERSON[0], PERSON[1], PERSON[2], PERSON[1] + height)
    points = tuple(
        TrackPoint(
            ts=EPOCH + timedelta(seconds=i / 2),
            frame_idx=i,
            box_xyxy=box,
            foot_px=((box[0] + box[2]) / 2, box[3]),
            confidence=0.9,
        )
        for i in range(20)
    )
    return Track(track_id=track_id, camera_id="cam1", concept="person", points=points)


def ppe_candidate(subject: str = "cam1:person:1") -> CandidateEvent:
    return make_candidate().model_copy(
        update={"rule_id": PPE.id, "predicate_trace": {"subject": subject}}
    )


class Frames:
    def __init__(self, n: int = 3) -> None:
        self.n = n
        self.asked: list[int] = []

    def frames(self, candidate: CandidateEvent, n: int) -> list[tuple[datetime, Frame]]:
        self.asked.append(n)
        return [
            (EPOCH + timedelta(seconds=2 * k + 1), np.zeros((480, 640, 3), np.uint8))
            for k in range(self.n)
        ]


class Crops:
    """Finds the hair net on the first head crop of the first two frames; never a glove."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[BoxXYXY], list[str]]] = []
        self.head_calls = 0

    def segment_crops(
        self, image: bytes, crops: Sequence[BoxXYXY], concepts: Sequence[str], *, camera_id: str
    ) -> list[CropHit]:
        assert image.startswith(b"\xff\xd8")
        self.calls.append((list(crops), list(concepts)))
        if "hair net" not in concepts:
            return []
        self.head_calls += 1
        if self.head_calls > 2:
            return []
        return [
            CropHit(crop_idx=0, concept="hair net", box_xyxy=(210, 90, 250, 110), confidence=0.64)
        ]


class Pose:
    def __init__(self) -> None:
        self.calls = 0

    def estimate(self, frame: Frame, box_xyxy: BoxXYXY) -> tuple[Keypoint, ...]:
        self.calls += 1
        wrists = {9: (190.0, 220.0), 10: (270.0, 220.0)}
        return tuple(
            Keypoint(
                x=wrists.get(k, (0, 0))[0],
                y=wrists.get(k, (0, 0))[1],
                score=0.9 if k in wrists else 0.0,
            )
            for k in range(17)
        )


def check(**kw: object) -> tuple[CropZoomCheck, Crops]:
    store = InMemoryTrackStore()
    store.append([person_track(), person_track("cam1:person:2", height=120.0)])
    crops = Crops()
    frames = kw.pop("frames", Frames())
    return CropZoomCheck(frames=frames, store=store, crops=crops, **kw), crops  # type: ignore[arg-type]


def test_zoom_items_come_from_the_rules_without_predicates_once_each() -> None:
    items = zoom_items(PPE)
    assert [(i.item, i.prompt, i.region) for i in items] == [
        ("hair_net", "hair net", "head"),
        ("glove", "disposable glove", "hands"),
    ]
    assert zoom_items(load_rule()) == ()


def test_findings_say_what_was_found_where_and_how_sure() -> None:
    tier0, crops = check(pose=Pose())
    lines = tier0(ppe_candidate(), PPE)
    assert lines[0].startswith("Close-up check (Tier 0, a detector's hints, not a verdict)")
    assert "head, hands at 3 keyframes" in lines[0]
    assert lines[1] == (
        "- 'hair net' (head): found in 2 of 3 head crops, at 2 of 3 keyframes, "
        "best confidence 0.64."
    )
    assert lines[2] == "- 'disposable glove' (hands): not found in any of 6 hands crops."
    head = [c for c, concepts in crops.calls if concepts == ["hair net"]]
    hands = [c for c, concepts in crops.calls if concepts == ["disposable glove"]]
    assert len(head) == 3
    assert len(hands) == 3
    assert all(len(c) == 1 for c in head)
    assert all(len(c) == 2 for c in hands)


def test_without_a_subject_in_the_trace_the_largest_people_are_checked() -> None:
    tracks = [person_track("a", 120.0), person_track("b", 200.0), person_track("c", 80.0)]
    candidate = ppe_candidate(subject="")
    assert [t.track_id for t in subject_tracks(candidate, tracks, {"person"})] == ["b", "a"]
    assert [t.track_id for t in subject_tracks(ppe_candidate("c"), tracks, {"person"})] == ["c"]


def test_no_without_predicate_no_frames_or_a_failure_add_nothing() -> None:
    tier0, crops = check()
    assert tier0(make_candidate(), load_rule()) == ()
    assert crops.calls == []
    empty, _ = check(frames=Frames(n=0))
    assert empty(ppe_candidate(), PPE) == ()

    class Broken(Frames):
        def frames(self, candidate: CandidateEvent, n: int) -> list[tuple[datetime, Frame]]:
            raise OSError("chunk gone")

    broken, _ = check(frames=Broken())
    assert broken(ppe_candidate(), PPE) == ()


def test_glimmer_gets_the_lines_before_the_question(tmp_path: Path) -> None:
    transport = QueueTransport(chat_reply(FIXTURE_ANSWER))
    base = make_verifier(transport)
    verifier = GlimmerVerifier(
        base._client, context=lambda c, r: ("Close-up check: hair net found.",)
    )
    verifier.verify(make_candidate(make_strip(tmp_path)), load_rule())
    body = transport.requests[0]["json"]
    assert isinstance(body, dict)
    text = next(p["text"] for p in body["messages"][1]["content"] if p["type"] == "text")
    assert text.index("Close-up check: hair net found.") < text.index("Question:")


def test_a_failing_context_is_ignored(tmp_path: Path) -> None:
    def broken(candidate: CandidateEvent, rule: object) -> tuple[str, ...]:
        raise RuntimeError("tier0 down")

    transport = QueueTransport(chat_reply(FIXTURE_ANSWER))
    verifier = GlimmerVerifier(make_verifier(transport)._client, context=broken)
    candidate = make_candidate(make_strip(tmp_path))
    assert verifier.verify(candidate, load_rule()).verdict == "violation"
    body = transport.requests[0]["json"]
    assert isinstance(body, dict)
    text = next(p["text"] for p in body["messages"][1]["content"] if p["type"] == "text")
    assert text == build_prompt(candidate, load_rule())


@pytest.mark.parametrize("subject", ["cam1:person:1"])
def test_hand_crops_follow_known_keypoints_without_calling_pose(subject: str) -> None:
    store = InMemoryTrackStore()
    kps = tuple(Keypoint(x=180.0 + k, y=200.0, score=0.9) for k in range(17))
    track = person_track(subject)
    track = track.model_copy(
        update={"points": tuple(p.model_copy(update={"keypoints": kps}) for p in track.points)}
    )
    store.append([track])
    pose, crops = Pose(), Crops()
    CropZoomCheck(frames=Frames(1), store=store, crops=crops, pose=pose)(ppe_candidate(), PPE)
    assert pose.calls == 0
    hands = next(c for c, concepts in crops.calls if concepts == ["disposable glove"])
    assert len(hands) == 2
    assert hands[0][0] < 189.0 < hands[0][2]
