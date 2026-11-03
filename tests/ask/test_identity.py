"""Ask's identity memory: track fragments joined by DINOv2 appearance, threshold from the data."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np

from rulecam.ask import FrameGrabber, TrackTables
from rulecam.ask.identity import (
    FALLBACK_THRESHOLD,
    MIN_THRESHOLD,
    IdentityMemory,
    _same_pairs,
    _Track,
    calibrate,
    crop_jpeg,
    link,
)
from rulecam.contracts import Track, TrackPoint
from rulecam.rules.store import ParquetTrackStore
from rulecam.runtime.factory import ChunkIndex
from tests.ask.test_ask import Chunks
from tests.fakes import EPOCH, FakeLLM, make_chunk


def unit(*values: float) -> np.ndarray:
    v = np.asarray(values, dtype=np.float32)
    return np.asarray(v / np.linalg.norm(v), dtype=np.float32)


def track(track_id: str, start_s: float, end_s: float) -> _Track:
    return _Track(
        track_id,
        "cam",
        "person",
        EPOCH + timedelta(seconds=start_s),
        EPOCH + timedelta(seconds=end_s),
    )


def test_fragments_that_look_alike_join_and_co_present_people_never_do() -> None:
    a, b = unit(1, 0, 0.1), unit(0, 1, 0.1)
    tracks = [
        track("cam:person:a1", 0, 5),
        track("cam:person:b1", 1, 6),
        track("cam:person:a2", 6, 9),
        track("cam:person:b2", 7, 12),
        track("cam:person:a3", 10, 15),
    ]
    vectors = {
        "cam:person:a1": a,
        "cam:person:a2": a,
        "cam:person:a3": unit(1, 0.05, 0.1),
        "cam:person:b1": b,
        "cam:person:b2": b,
    }
    linked, threshold = link(tracks, vectors)
    identities = {t: i for t, (i, _) in linked.items()}
    assert identities["cam:person:a1"] == identities["cam:person:a2"] == identities["cam:person:a3"]
    assert identities["cam:person:b1"] == identities["cam:person:b2"]
    assert identities["cam:person:a1"] != identities["cam:person:b1"]
    assert threshold == FALLBACK_THRESHOLD  # only a few co-present pairs: no distribution to learn


def test_the_threshold_is_learned_from_objects_seen_together() -> None:
    rng = np.random.default_rng(0)
    crowd = [track(f"cam:person:p{k}", 0, 10) for k in range(6)]  # 15 co-present pairs
    vectors = {t.track_id: unit(*rng.normal(size=8)) for t in crowd}
    later = track("cam:person:late", 11, 14)
    vectors[later.track_id] = vectors["cam:person:p0"]
    linked, threshold = link([*crowd, later], vectors)
    impostors = [
        float(vectors[a.track_id] @ vectors[b.track_id])
        for i, a in enumerate(crowd)
        for b in crowd[i + 1 :]
    ]
    assert threshold >= max(0.5, float(np.quantile(impostors, 0.99))) - 1e-3
    assert len({i for i, _ in linked.values()}) == 6  # the crowd stays six; "late" joins p0
    assert linked["cam:person:late"][0] == linked["cam:person:p0"][0]


def test_a_similar_fragment_that_overlaps_in_time_starts_its_own_identity() -> None:
    a = unit(1, 0)
    linked, _ = link(
        [track("cam:person:x", 0, 5), track("cam:person:y", 3, 8)],
        {"cam:person:x": a, "cam:person:y": a},
    )
    assert linked["cam:person:x"][0] != linked["cam:person:y"][0]


def test_crops_are_cut_with_padding_and_tiny_boxes_skipped(tmp_path: Path) -> None:
    frame = tmp_path / "f.jpg"
    cv2.imwrite(str(frame), np.full((720, 1280, 3), 128, dtype=np.uint8))
    data = crop_jpeg(frame, (100.0, 100.0, 200.0, 400.0))
    assert data is not None
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    assert image.shape[:2] == (348, 116)
    assert crop_jpeg(frame, (10.0, 10.0, 12.0, 12.0)) is None


def point(at_s: float, x: float) -> TrackPoint:
    return TrackPoint(
        ts=EPOCH + timedelta(seconds=at_s),
        frame_idx=0,
        box_xyxy=(x, 100.0, x + 100.0, 400.0),
        foot_px=(x + 50, 400.0),
    )


def store_tracks(root: Path) -> None:
    def make(track_id: str, start: float, end: float, x: float) -> Track:
        points = tuple(point(start + k * 0.5, x) for k in range(int((end - start) * 2) + 1))
        return Track(track_id=track_id, camera_id="checkout", concept="person", points=points)

    with ParquetTrackStore(root) as store:
        store.append(
            [
                make("checkout:person:a1", 0, 4, 100),
                make("checkout:person:a2", 5, 9, 110),
                make("checkout:person:b1", 0, 9, 700),
                make("checkout:person:late", 20, 25, 300),
            ]
        )


def test_a_run_writes_identities_spans_and_notes(tmp_path: Path) -> None:
    root = tmp_path / "tracks"
    store_tracks(root)

    def frame_writer(chunk: Path, indices: Sequence[int], out_dir: Path) -> None:
        cv2.imwrite(str(out_dir / "1.jpg"), np.full((720, 1280, 3), 90, dtype=np.uint8))

    seen: list[int] = []

    def embed(crops: list[bytes]) -> list[list[float]]:
        seen.append(len(crops))
        return [[1.0, 0.0] for _ in crops]  # everyone looks alike: only time keeps b1 apart

    glimmer = FakeLLM([{"role": "customer", "description": "red shirt", "carrying": "trolley"}] * 6)
    memory = IdentityMemory(
        tracks_root=root,
        frames=FrameGrabber(Chunks(), tmp_path / "f", extract=frame_writer),  # type: ignore[arg-type]
        embed=embed,
        glimmer=glimmer,
        glimmer_model="muse-glimmer-30b",
        out_dir=tmp_path / "id",
        site_profile="Supermarket",
    )
    memory.run()

    rows = [json.loads(line) for line in memory.identities_path.read_text().splitlines()]
    by_track = {r["track_id"]: r["identity_id"] for r in rows}
    assert "checkout:person:late" not in by_track  # still being recorded: not finished
    assert by_track["checkout:person:a1"] == by_track["checkout:person:a2"]
    assert by_track["checkout:person:b1"] != by_track["checkout:person:a1"]
    assert sum(seen) == 9  # three crops per finished track
    notes = memory.notes_path.read_text().splitlines()
    assert len(notes) == 2
    tables = TrackTables(root, identities=memory.identities_path, notes=memory.notes_path)
    spans = tables.query("SELECT fragments, seconds FROM identity_spans ORDER BY fragments DESC")
    assert spans.rows[0] == [2, 9.0]
    assert tables.query("SELECT DISTINCT role FROM identity_notes").rows == [["customer"]]
    tables.close()
    with memory.notes_path.open("a") as out:
        out.write(
            json.dumps(
                {"identity_id": "gone", "role": "customer", "description": "x", "carrying": ""}
            )
            + "\n"
        )
    tables = TrackTables(root, identities=memory.identities_path, notes=memory.notes_path)
    assert tables.query("SELECT count(*) FROM identity_notes").rows == [
        [2]
    ]  # the retired one is dropped
    tables.close()
    memory.run()  # a second run embeds nothing new
    assert sum(seen) == 9


def test_a_track_that_cannot_be_cropped_is_not_retried(tmp_path: Path) -> None:
    root = tmp_path / "tracks"
    store_tracks(root)
    calls: list[int] = []

    def embed(crops: list[bytes]) -> list[list[float]]:
        calls.append(len(crops))
        return [[] for _ in crops]  # nothing usable comes back

    def frame_writer(chunk: Path, indices: Sequence[int], out_dir: Path) -> None:
        cv2.imwrite(str(out_dir / "1.jpg"), np.full((720, 1280, 3), 90, dtype=np.uint8))

    memory = IdentityMemory(
        tracks_root=root,
        frames=FrameGrabber(Chunks(), tmp_path / "f", extract=frame_writer),  # type: ignore[arg-type]
        embed=embed,
        glimmer=None,
        glimmer_model="muse-glimmer-30b",
        out_dir=tmp_path / "id",
    )
    memory.run()
    memory.run()
    assert sum(calls) == 9  # tried once, never again


def test_the_chunk_journal_survives_a_restart(tmp_path: Path) -> None:
    journal = tmp_path / "chunks" / "index.jsonl"
    video = tmp_path / "c0.mp4"
    video.write_bytes(b"x")
    first = ChunkIndex(journal=journal)
    first.record(make_chunk("checkout", 0).model_copy(update={"path": str(video)}))
    first.record(make_chunk("checkout", 1).model_copy(update={"path": str(tmp_path / "gone.mp4")}))
    again = ChunkIndex(journal=journal)
    assert [c.chunk_id for c in again.chunks("checkout", EPOCH, EPOCH + timedelta(hours=1))] == [
        "checkout_000000"
    ]


def test_calibration_puts_the_threshold_where_the_two_kinds_of_pairs_cross() -> None:
    same = [0.95, 0.9, 0.88, 0.86, 0.84, 0.83, 0.82, 0.8, 0.78, 0.76]
    different = [0.4, 0.5, 0.55, 0.6, 0.62, 0.65, 0.7, 0.72, 0.74, 0.79]
    threshold = calibrate(same, different)
    assert 0.74 < threshold <= 0.8
    assert calibrate(same[:3], different) == max(MIN_THRESHOLD, float(np.quantile(different, 0.99)))
    assert calibrate(same, different[:3]) == FALLBACK_THRESHOLD


def test_a_fragment_starting_where_another_ended_is_a_same_pair() -> None:
    a = _Track(
        "c:p:a", "c", "p", EPOCH, EPOCH + timedelta(seconds=4), end=(100.0, 400.0), width=80.0
    )
    b = _Track(
        "c:p:b",
        "c",
        "p",
        EPOCH + timedelta(seconds=4.5),
        EPOCH + timedelta(seconds=8),
        start=(120.0, 405.0),
    )
    far = _Track(
        "c:p:f",
        "c",
        "p",
        EPOCH + timedelta(seconds=4.5),
        EPOCH + timedelta(seconds=8),
        start=(600.0, 405.0),
    )
    late = _Track(
        "c:p:l",
        "c",
        "p",
        EPOCH + timedelta(seconds=6),
        EPOCH + timedelta(seconds=8),
        start=(110.0, 400.0),
    )
    assert [(x.track_id, y.track_id) for x, y in _same_pairs([a, b, far, late])] == [
        ("c:p:a", "c:p:b")
    ]
