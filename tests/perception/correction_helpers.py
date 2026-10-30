"""A UC2-style scene for correction tests: a backpack strap mis-read as a harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rulecam.contracts import Chunk, Detection, EventRecord, Mask, RuleIR, Track
from rulecam.perception.correction import CorrectionService
from rulecam.rules.backtest import DecisionContext, candidate_payload
from tests.fakes import FakeSegmenter, InMemoryTrackStore, make_chunk
from tests.rules.helpers import at, box_track, make_rule

PERSON_BOX = (100.0, 0.0, 200.0, 300.0)
STRAP_BOX = (130.0, 80.0, 170.0, 140.0)  # inside the person box
STRAP_CENTRE = (150.0, 110.0)
UNCLIPPED = make_rule(
    {
        "op": "all",
        "of": [
            {"op": "overlap", "a": "person", "b": "harness"},
            {"op": "without", "subject": "person", "item": "lanyard", "region": "full"},
        ],
    },
    concepts={"person": "person", "harness": "safety harness", "lanyard": "lanyard"},
    rule_id="harness-unclipped",
    severity="high",
)


def scene_tracks() -> list[Track]:
    return [
        box_track("cam1:person:1", [PERSON_BOX] * 60, concept="person"),
        box_track("cam1:harness:1", [STRAP_BOX] * 60, concept="safety harness"),
    ]


def strap_detections(chunk: Chunk, concept: str) -> list[Detection]:
    """What SAM returns without a correction: the strap, labelled as a harness."""
    return [
        Detection(
            camera_id=chunk.camera_id,
            frame_idx=i,
            ts=chunk.start_ts + timedelta(seconds=i / chunk.fps),
            concept=concept,
            object_id=1,
            mask=Mask(rle="rle", box_xyxy=STRAP_BOX, confidence=0.8),
        )
        for i in range(chunk.n_frames)
    ]


class Events:
    def __init__(self, records: dict[str, EventRecord]) -> None:
        self.records = records

    def find(self, event_id: str) -> EventRecord | None:
        return self.records.get(event_id)


class Chunks:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.items = chunks

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        return [
            c
            for c in self.items
            if c.camera_id == camera_id
            and c.start_ts <= t1
            and c.start_ts + timedelta(seconds=c.n_frames / c.fps) > t0
        ]


@dataclass
class Labels:
    records: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def __call__(self, kind: str, payload: dict[str, object]) -> object:
        self.records.append((kind, payload))
        return len(self.records)


@dataclass
class Harness:
    """Everything a correction test needs, wired together."""

    service: CorrectionService
    segmenter: FakeSegmenter
    labels: Labels
    record: EventRecord
    context: DecisionContext
    events: Events


def build(segmenter: FakeSegmenter | None = None, rule: RuleIR = UNCLIPPED) -> Harness:
    tracks = scene_tracks()
    context = DecisionContext(rule=rule, t0=at(0), t1=at(6))
    (candidate,) = context.evaluate(tracks)
    record = EventRecord(
        seq=1,
        kind="candidate",
        ts=candidate.ts_start,
        site="yard",
        payload=candidate_payload(candidate, context),
        prev_hash="0" * 64,
        hash="1" * 64,
    )
    store = InMemoryTrackStore()
    store.append(tracks)
    fake = segmenter or FakeSegmenter()
    labels = Labels()
    events = Events({candidate.event_id: record})
    service = CorrectionService(
        segmenter=fake,
        store=store,
        events=events,
        chunks=Chunks([make_chunk("cam1", 0), make_chunk("cam1", 1), make_chunk("cam1", 2)]),
        record_label=labels,
    )
    return Harness(service, fake, labels, record, context, events)
