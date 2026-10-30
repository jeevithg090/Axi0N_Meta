"""`ParquetTrackStore`: round trip, restart, windowing, partitions and retention."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rulecam.contracts import Keypoint, Provenance, Track, TrackPoint, TrackStore
from rulecam.rules.store import (
    COLUMNS,
    PERCEPTION_INTERNAL_VIEWS,
    ParquetTrackStore,
)
from tests.fakes import EPOCH, InMemoryTrackStore, make_track


def rich_track() -> Track:
    """A track exercising every optional column."""
    base = make_track("cam1:person:aaaa0001", feet_m=[(0.5, 1.25), (0.75, 1.5), (1.0, 1.75)])
    points = list(base.points)
    points[1] = points[1].model_copy(
        update={
            "foot_m": None,
            "confidence": None,
            "keypoints": (Keypoint(x=1.5, y=2.25, score=0.875), Keypoint(x=3.0, y=4.0, score=0.5)),
        }
    )
    points[2] = points[2].model_copy(update={"ts": points[2].ts + timedelta(microseconds=123)})
    return base.model_copy(update={"points": tuple(points)})


def test_store_conforms_to_track_store(tmp_path: Path) -> None:
    store: TrackStore = ParquetTrackStore(tmp_path)

    assert store.window("cam1", EPOCH, EPOCH) == []


def test_round_trip_gives_identical_points(tmp_path: Path) -> None:
    track = rich_track()
    with ParquetTrackStore(tmp_path) as store:
        store.append([track])

        [back] = store.window("cam1", EPOCH, EPOCH + timedelta(seconds=10))

    assert back == track
    assert back.points[0].ts.tzinfo is UTC


def test_store_survives_a_restart(tmp_path: Path) -> None:
    first = ParquetTrackStore(tmp_path)
    first.append([rich_track(), make_track("cam1:hard hat:bbbb0002", concept="hard hat")])
    first.close()

    with ParquetTrackStore(tmp_path) as reopened:
        tracks = reopened.window("cam1", EPOCH, EPOCH + timedelta(seconds=10))

    assert [t.track_id for t in tracks] == ["cam1:hard hat:bbbb0002", "cam1:person:aaaa0001"]


def test_points_of_one_track_from_several_chunks_are_merged(tmp_path: Path) -> None:
    whole = make_track(feet_m=[(float(i), 0.0) for i in range(6)])
    with ParquetTrackStore(tmp_path) as store:
        store.append([whole.model_copy(update={"points": whole.points[3:]})])
        store.append([whole.model_copy(update={"points": whole.points[:3]})])

        assert store.window("cam1", EPOCH, EPOCH + timedelta(seconds=1)) == [whole]
        clipped = store.window(
            "cam1", EPOCH + timedelta(seconds=0.1), EPOCH + timedelta(seconds=0.3)
        )
    assert [p.frame_idx for p in clipped[0].points] == [1, 2, 3]


def test_window_filters_by_camera_and_handles_empty_ranges(tmp_path: Path) -> None:
    with ParquetTrackStore(tmp_path) as store:
        store.append(
            [make_track(), make_track("cam2:person:0002", camera_id="cam2"), make_track(feet_m=[])]
        )

        assert [t.camera_id for t in store.window("cam2", EPOCH, EPOCH)] == ["cam2"]
        assert store.window("cam1", EPOCH + timedelta(seconds=1), EPOCH) == []
        assert store.window("cam3", EPOCH, EPOCH) == []


def test_partitions_by_camera_and_date_and_records_provenance(tmp_path: Path) -> None:
    near_midnight = datetime(2026, 10, 30, 23, 59, 59, 900_000, tzinfo=UTC)
    track = make_track(feet_m=[(0, 0), (1, 1), (2, 2)], start=near_midnight)
    with ParquetTrackStore(tmp_path, provenance=Provenance.SIMULATED) as store:
        store.append([track])
        provenance = store.connection.sql("SELECT DISTINCT provenance FROM track_points").fetchall()

    dirs = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.glob("*/*"))
    assert dirs == ["camera_id=cam1/date=2026-10-30", "camera_id=cam1/date=2026-10-31"]
    assert provenance == [("simulated",)]


def test_views_have_the_documented_columns(tmp_path: Path) -> None:
    with ParquetTrackStore(tmp_path) as store:
        empty = store.connection.sql("SELECT * FROM track_points")
        assert tuple(empty.columns) == COLUMNS
        store.append([make_track(feet_m=[(0, 0)] * 3)])
        catalogue = store.connection.sql("SELECT * FROM tracks").fetchall()
        views = {
            name
            for (name,) in store.connection.sql(
                "SELECT view_name FROM duckdb_views() WHERE NOT internal AND NOT temporary"
            ).fetchall()
        }

    assert catalogue == [("cam1:person:0001", "cam1", "person")]
    assert views == PERCEPTION_INTERNAL_VIEWS


@pytest.mark.parametrize("camera_id", ["../escape", "cam/1", "", ".hidden"])
def test_unsafe_camera_ids_are_rejected(tmp_path: Path, camera_id: str) -> None:
    with ParquetTrackStore(tmp_path) as store, pytest.raises(ValueError, match="partition"):
        store.append([make_track(camera_id=camera_id)])


def test_naive_timestamps_are_rejected(tmp_path: Path) -> None:
    naive = make_track(start=EPOCH.replace(tzinfo=None))
    with ParquetTrackStore(tmp_path) as store:
        with pytest.raises(ValueError, match="timezone-aware"):
            store.append([naive])
        with pytest.raises(ValueError, match="timezone-aware"):
            store.window("cam1", EPOCH.replace(tzinfo=None), EPOCH)


def test_retention_deletes_whole_old_days(tmp_path: Path) -> None:
    days = [EPOCH - timedelta(days=n) for n in (0, 29, 31, 40)]
    with ParquetTrackStore(tmp_path) as store:
        store.append([make_track(f"cam1:person:{n:04d}", start=day) for n, day in enumerate(days)])

        removed = store.apply_retention(30, now=EPOCH)
        kept = store.window("cam1", EPOCH - timedelta(days=60), EPOCH + timedelta(days=1))

        assert store.apply_retention(30, now=EPOCH) == 0
    assert removed == 2
    assert [t.track_id for t in kept] == ["cam1:person:0001", "cam1:person:0000"]


# --- Hypothesis: window(t0, t1) never returns points outside [t0, t1] -----------------------------

offsets_ms = st.integers(min_value=0, max_value=2 * 86_400_000)  # two days, so dates vary


@st.composite
def stored_tracks(draw: st.DrawFn) -> list[Track]:
    tracks = []
    for n in range(draw(st.integers(min_value=1, max_value=4))):
        stamps = sorted(set(draw(st.lists(offsets_ms, min_size=1, max_size=8))))
        points = tuple(
            TrackPoint(
                ts=EPOCH + timedelta(milliseconds=ms),
                frame_idx=i,
                box_xyxy=(0.0, 0.0, 1.0, 1.0),
                foot_px=(0.5, 1.0),
            )
            for i, ms in enumerate(stamps)
        )
        camera = draw(st.sampled_from(["cam1", "cam2"]))
        tracks.append(
            Track(
                track_id=f"{camera}:person:{n:08x}",
                camera_id=camera,
                concept="person",
                points=points,
            )
        )
    return tracks


@settings(max_examples=40, deadline=None)
@given(tracks=stored_tracks(), a=offsets_ms, b=offsets_ms)
def test_window_never_returns_points_outside_the_range(tracks: list[Track], a: int, b: int) -> None:
    t0 = EPOCH + timedelta(milliseconds=min(a, b))
    t1 = EPOCH + timedelta(milliseconds=max(a, b))
    reference = InMemoryTrackStore()
    reference.append(tracks)
    with tempfile.TemporaryDirectory() as root, ParquetTrackStore(Path(root)) as store:
        store.append(tracks)
        got = store.window("cam1", t0, t1)

    assert all(t0 <= p.ts <= t1 for t in got for p in t.points)
    assert all(t.points for t in got)
    expected = sorted(reference.window("cam1", t0, t1), key=lambda t: (t.points[0].ts, t.track_id))
    assert got == expected
