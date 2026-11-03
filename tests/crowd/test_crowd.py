"""Tests for festival crowd watch: density, episodes, the safety-asymmetric decision, routes."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.ask.watch import Clip
from rulecam.clients.errors import ModelResponseError
from rulecam.crowd.api import crowd_scan_router
from rulecam.crowd.density import CrowdLevels, Episode, episodes, peak_local_density, sample
from rulecam.crowd.watch import MAX_CHECKS, CrowdCheck, CrowdError, CrowdWatch, _thin, decide
from tests.fakes import FakeLLM

T0 = datetime(2026, 11, 3, 21, 0, tzinfo=UTC)
LEVELS = CrowdLevels()


def packed(n: int, spacing: float = 0.35) -> list[tuple[float, float]]:
    return [(10 + (i % 6) * spacing, 5 + (i // 6) * spacing) for i in range(n)]


def test_peak_local_density_finds_the_densest_disc() -> None:
    assert peak_local_density([]) == (0.0, None)
    sparse = [(0.0, 0.0), (5.0, 5.0), (10.0, 0.0)]
    assert peak_local_density(sparse)[0] == pytest.approx(1 / math.pi)
    dense, centre = peak_local_density(packed(18) + sparse)
    assert dense > 4.0
    assert centre is not None
    assert 10 <= centre[0] <= 12


def test_levels() -> None:
    assert [LEVELS.level(d) for d in (0.5, 2.0, 4.2, 5.0)] == [
        "normal",
        "busy",
        "crowded",
        "critical",
    ]
    assert sample(T0, packed(18), LEVELS).level == "crowded"
    assert sample(T0, [], LEVELS).peak_at_m is None


def samples(levels: Sequence[float], step_s: float = 1.0) -> list[Any]:
    """One sample per second at the given densities (people scaled to match)."""
    out = []
    for k, d in enumerate(levels):
        out.append(
            sample(T0 + timedelta(seconds=k * step_s), packed(max(1, round(d * math.pi))), LEVELS)
        )
    return out


def test_episodes_need_to_last_and_bridge_short_dips() -> None:
    dense = [4.8] * 6 + [1.0] * 2 + [4.8] * 6 + [0.5] * 10 + [4.8] * 3
    found = episodes(samples(dense), min_seconds=10.0, max_gap_s=5.0)
    assert len(found) == 1
    assert found[0].seconds == 13.0
    assert found[0].level == "crowded"
    assert episodes(samples(dense), min_seconds=10.0, max_gap_s=1.0) == []
    critical = episodes(samples([4.5] * 5 + [6.5] * 10), min_seconds=5.0)
    assert critical[0].level == "critical"
    assert critical[0].peak_density > 5.0


def episode(level: str = "crowded", density: float = 4.5) -> Episode:
    return Episode.model_validate(
        {
            "start": T0,
            "end": T0 + timedelta(seconds=12),
            "level": level,
            "peak_density": density,
            "peak_ts": T0 + timedelta(seconds=3),
            "peak_people": 18,
        }
    )


def check(**overrides: Any) -> CrowdCheck:
    data: dict[str, Any] = {
        "dense": True,
        "movement": "slow",
        "signs": [],
        "summary": "dense crowd at the barrier",
        "confidence": 0.8,
    }
    return CrowdCheck.model_validate(data | overrides)


def test_the_video_check_can_add_reasons_never_remove_them() -> None:
    assert decide(episode(), None) == (False, False, [])
    assert decide(episode("critical", 6.0), check(dense=False))[:2] == (True, True)
    escalate, disputed, reasons = decide(
        episode(),
        check(
            movement="surging",
            signs=[
                {"sign": "person_down", "at_s": 4, "what": "someone fell"},
                {"sign": "counter_flow", "at_s": 2, "what": "people moving back"},
            ],
        ),
    )
    assert escalate
    assert not disputed
    assert reasons == ["video shows person down", "video shows the crowd surging"]


class Tables:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.params: list[object] = []

    def __call__(self, _root: Path) -> Tables:
        return self

    def query_params(self, _sql: str, params: Sequence[object]) -> list[tuple[object, ...]]:
        self.params = list(params)
        return self.rows


def dense_rows(seconds: int = 15, people: int = 18) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for k in range(seconds):
        ts = (T0 + timedelta(seconds=k)).replace(tzinfo=None)
        rows += [(ts, x, y) for x, y in packed(people)]
    rows.append(((T0 + timedelta(seconds=1)).replace(tzinfo=None), None, None))
    return rows


class Joiner:
    def __init__(self, tmp_path: Path, fail: bool = False) -> None:
        self.path = tmp_path / "clip.mp4"
        self.path.write_bytes(b"mp4")
        self.fail = fail
        self.windows: list[tuple[datetime, datetime]] = []

    def clip(self, camera_id: str, start: datetime, end: datetime) -> Clip:
        self.windows.append((start, end))
        if self.fail:
            raise ValueError("nothing recorded")
        return Clip(camera_id=camera_id, path=self.path, start=start, end=end, chunks=2)


class Files:
    def attach(self, path: Path) -> dict[str, object]:
        return {"type": "input_video", "file_id": path.name}


def watch(tmp_path: Path, tables: Tables, spark: FakeLLM | None, joiner: Joiner) -> CrowdWatch:
    return CrowdWatch(
        tracks_root=tmp_path,
        spark=spark,
        files=Files(),
        joiner=joiner,  # type: ignore[arg-type]
        tables=tables,  # type: ignore[arg-type]
    )


def test_scan_checks_each_episode_on_video(tmp_path: Path) -> None:
    signs = [{"sign": "pushing_at_barrier", "at_s": 4.0, "what": "pressed on the barrier"}]
    spark = FakeLLM([check(signs=signs)])
    joiner = Joiner(tmp_path)
    tables = Tables(dense_rows())
    report = watch(tmp_path, tables, spark, joiner).scan(
        "main-stage", T0 - timedelta(seconds=5), T0 + timedelta(seconds=60)
    )
    assert tables.params[0] == "main-stage"
    assert isinstance(tables.params[2], datetime)
    assert tables.params[2].tzinfo is None
    assert report.calibrated
    assert report.keyframes == 15
    (checked,) = report.episodes
    assert checked.escalate
    assert checked.reasons == ["video shows pushing at barrier"]
    assert checked.moments[0]["sign"] == "pushing_at_barrier"
    assert checked.clip_seconds == 24.0
    assert joiner.windows[0][0] == T0 - timedelta(seconds=5)
    assert "1 person points had no metric position" in " ".join(report.notes)
    messages: Any = spark.calls[0]["messages"]
    content = messages[1]["content"]
    assert content[1] == {"type": "input_video", "file_id": "clip.mp4"}
    assert "18 people" in content[0]["text"]


def test_a_failed_video_check_keeps_the_density_decision(tmp_path: Path) -> None:
    rows: list[tuple[object, ...]] = [
        ((T0 + timedelta(seconds=k)).replace(tzinfo=None), x, y)
        for k in range(15)
        for x, y in packed(25, spacing=0.25)
    ]
    report = watch(tmp_path, Tables(rows), FakeLLM([]), Joiner(tmp_path, fail=True)).scan(
        "cam", T0, T0 + timedelta(seconds=30)
    )
    (checked,) = report.episodes
    assert checked.episode.level == "critical"
    assert checked.escalate
    assert checked.check is None
    assert checked.check_error == "ValueError: nothing recorded"


def test_scan_without_spark_or_calibration(tmp_path: Path) -> None:
    report = watch(tmp_path, Tables(dense_rows()), None, Joiner(tmp_path)).scan(
        "cam", T0, T0 + timedelta(seconds=30)
    )
    assert report.episodes[0].check is None
    assert any("video checks are off" in n for n in report.notes)
    blind: list[tuple[object, ...]] = [
        ((T0 + timedelta(seconds=k)).replace(tzinfo=None), None, None) for k in range(3)
    ]
    uncal = watch(tmp_path, Tables(blind), None, Joiner(tmp_path)).scan(
        "cam", T0, T0 + timedelta(seconds=9)
    )
    assert not uncal.calibrated
    assert any("calibrate this camera" in n for n in uncal.notes)
    empty = watch(tmp_path, Tables([]), None, Joiner(tmp_path)).scan(
        "cam", T0, T0 + timedelta(seconds=9)
    )
    assert any("no tracked people" in n for n in empty.notes)
    with pytest.raises(CrowdError):
        watch(tmp_path, Tables([]), None, Joiner(tmp_path)).scan("cam", T0, T0)


def test_only_the_densest_episodes_are_checked(tmp_path: Path) -> None:
    rows: list[tuple[object, ...]] = []
    for n in range(MAX_CHECKS + 1):
        start = T0 + timedelta(minutes=n)
        rows += [
            ((start + timedelta(seconds=k)).replace(tzinfo=None), x, y)
            for k in range(12)
            for x, y in packed(15 + n)
        ]
    spark = FakeLLM([check() for _ in range(MAX_CHECKS)])
    report = watch(tmp_path, Tables(rows), spark, Joiner(tmp_path)).scan(
        "cam", T0, T0 + timedelta(minutes=10)
    )
    assert len(report.episodes) == MAX_CHECKS + 1
    assert report.episodes[0].check is None  # the least dense one
    assert all(e.check is not None for e in report.episodes[1:])
    assert any(f"only the {MAX_CHECKS} densest" in n for n in report.notes)


def test_thin_keeps_the_densest_sample() -> None:
    many = samples([1.0] * 50 + [6.0] + [1.0] * 49)
    thinned = _thin(many, 10)
    assert len(thinned) <= 11
    assert max(s.peak_density for s in thinned) == max(s.peak_density for s in many)
    assert _thin(many[:5], 10) == many[:5]


def test_route(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(
        crowd_scan_router(watch(tmp_path, Tables(dense_rows()), None, Joiner(tmp_path)))
    )
    body = {
        "camera_id": "cam",
        "start": T0.isoformat(),
        "end": (T0 + timedelta(minutes=1)).isoformat(),
    }
    with TestClient(app) as client:
        reply = client.post("/crowd/scan", json=body)
        assert reply.status_code == 200
        assert reply.json()["episodes"][0]["episode"]["level"] == "crowded"
        long = body | {"end": (T0 + timedelta(hours=7)).isoformat()}
        assert client.post("/crowd/scan", json=long).status_code == 422
        backwards = body | {"end": T0.isoformat()}
        assert client.post("/crowd/scan", json=backwards).status_code == 422
        assert client.post("/crowd/scan", json=body | {"camera_id": "../x"}).status_code == 422
    off = FastAPI()
    off.include_router(crowd_scan_router(None))
    with TestClient(off) as client:
        assert client.post("/crowd/scan", json=body).status_code == 503


def test_spark_failure_is_named(tmp_path: Path) -> None:
    class Failing(FakeLLM):
        def structured(self, **kwargs: Any) -> Any:
            raise ModelResponseError("video too long")

    report = watch(tmp_path, Tables(dense_rows()), Failing(), Joiner(tmp_path)).scan(
        "cam", T0, T0 + timedelta(seconds=30)
    )
    assert report.episodes[0].check_error == "ModelResponseError: video too long"
